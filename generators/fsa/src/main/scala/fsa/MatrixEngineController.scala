package fsa

import chisel3._
import chisel3.util._
import org.chipsalliance.cde.config.Parameters
import fsa.sa._
import fsa.arithmetic._
import fsa.frontend.Semaphore
import fsa.isa._
import fsa.utils.{DelayedAssert, Ehr}
import chisel3.experimental.SourceInfo

object SpadConstIdx {
  def width = 2
  def ONE = 0
  def AttentionScale = 1
  def Exp2Slopes = 2
}

object FanoutSplit {
  // A/B experiment switch for the M1 §4.0 fanout split.
  //   false = exact pre-split (p3) behavior: single compute/accum timer +
  //           single acc_ctrl pipe register fanning out to all lanes
  //   true  = replicated timer banks + per-lane acc_ctrl pipe registers
  // Used to bisect an unexpected mxBubble delta (91 -> 122) between the
  // two behaviors; both must be bit-identical, any counter delta is a bug.
  // Elaboration-only switch: one compiled generator serves both A/B variants.
  val enabled: Boolean = sys.props.get("fsa.fanoutSplit") match {
    case None | Some("true") => true
    case Some("false") => false
    case Some(value) => throw new IllegalArgumentException(s"Invalid fsa.fanoutSplit=$value")
  }
}

object AccConstIdx {
  def width = 1
  def ZERO = 0
}

trait CanReadConstant {
  val is_constant = Bool()
}

class SpRead()(implicit p: Parameters) extends FSABundle with CanReadConstant {
  val addr = UInt(SPAD_ROW_ADDR_WIDTH.W)
  val rev_sram_out = Bool()
  val delay_sram_out = Bool()
  val rev_delayer_out = Bool()
}

class AccRead()(implicit p: Parameters) extends FSABundle with CanReadConstant {
  val addr = UInt(ACC_ROW_ADDR_WIDTH.W)
  val const_idx = UInt(AccConstIdx.width.W)
  // read-modify-write, write back SRAM next cycle if set to 1
  val rmw = Bool()
}


class MatrixControllerIO(implicit p: Parameters) extends FSABundle {
  val in = Flipped(Decoupled(new MatrixInstruction(SPAD_ROW_ADDR_WIDTH, ACC_ROW_ADDR_WIDTH)))
  val sp_read = Valid(new SpRead)
  val acc_read = Valid(new AccRead())
  val cmp_ctrl = Valid(new CmpControl)
  val pe_ctrl = Vec(SA_ROWS, Valid(new PECtrl))
  // Fanout split (M1 §4.0): the accumulator control is decoded by every
  // accumulator lane, so it is provided per lane.  Each element is driven by
  // its OWN pipeline-register copy (see below), all copies loaded with the
  // same value on the same cycle -- delay-neutral, no schedule change.
  val acc_ctrl = Vec(SA_COLS, Valid(new AccumulatorControl))
  val sem_release = Valid(new Semaphore)
  val busy = Output(Bool())
}

class MatrixControlFSM
(
  planFunc: Seq[UInt],
  executionPlans: Seq[ExecutionPlan]
)(implicit p: Parameters) extends FSAModule {
  val io = IO(new MatrixControllerIO() {
    val conflictFree = Output(Bool())
  })

  val header = RegEnable(io.in.bits.header, io.in.fire)
  val rs1 = RegEnable(io.in.bits.spad, io.in.fire)
  val rs2 = RegEnable(io.in.bits.acc, io.in.fire)

  val conflictFreeFlag = Ehr(2, Bool(), Some(false.B))
  val computeFlags = executionPlans.map(_ => RegInit(false.B))
  val accumFlags = executionPlans.map(_ => RegInit(false.B))

  val computeValid = Cat(computeFlags).orR
  val accumValid = Cat(accumFlags).orR

  val nPlans = executionPlans.length

  // Timer banks (M1 §4.0 fanout split): k physically independent register
  // copies holding the SAME value, updated by identical logic every cycle.
  // Purely delay-neutral: each consumer group reads its own copy, so the
  // ~1000-load decode cone of a single timer register disappears.
  // dontTouch is essential: without it synthesis collapses the equivalent
  // copies back into one register and the split is lost.
  def timerBank(k: Int, width: Int): Seq[UInt] = {
    val n = if (FanoutSplit.enabled) k else 1
    val ts = Seq.fill(n)(RegInit(0.U(width.W)))
    if (FanoutSplit.enabled) ts.foreach(t => dontTouch(t))
    ts
  }

  // bank access through the switch: index 0 in the disabled case makes the
  // single physical copy behave exactly like the original single timer
  def bankGet(bank: Seq[UInt], i: Int): UInt = if (FanoutSplit.enabled) bank(i) else bank(0)

  // compute timer: one copy per execution plan (genPECtrl + done compare)
  // plus dedicated copies for the sp_read and cmp_ctrl select() calls
  val computeTimerBank = timerBank(nPlans + 2,
    executionPlans.map(_.computeMaxCycle).max.U.getWidth)
  // accumulate timer: one copy per execution plan (done/conflictFree compare)
  // plus dedicated copies for acc_read / acc_ctrl / sem_release
  // (a separate timer from compute to reduce comparison latency)
  val accumTimerBank = timerBank(nPlans + 3,
    executionPlans.map(plan =>
      plan.accumulateMaxCycle - plan.accStartCycle
    ).max.U.getWidth)

  // PE Control (each plan reads its own compute-timer copy)
  computeFlags.zip(executionPlans).zipWithIndex.map{ case ((flag, plan), i) =>
    plan.genPECtrl(bankGet(computeTimerBank, i), flag)
  }.transpose.map(row =>
    row.map(ctrl => ctrl.asUInt).reduce(_|_).asTypeOf(new PECtrl)
  ).zip(io.pe_ctrl).foreach{ case (generated, pe_ctrl) =>
    pe_ctrl.bits := generated
    pe_ctrl.valid := generated.asUInt.orR
  }


  def select[T <: Data]
  (
    timer: UInt,
    flags: Seq[Bool], ctrlDesc: Seq[Iterable[CanGenerateHw[T] with HasEffRange]],
    timerBaseOpt: Option[Seq[Int]] = None): Valid[T] =
  {
    val timerBase = timerBaseOpt.getOrElse(flags.map(_ => 0))
    val (valid, ctrl) = flags.zip(ctrlDesc).zip(timerBase).flatMap { case ((flag, descSeq), tBase) =>
      descSeq.map{ desc =>
        (flag && desc.valid(timer, tBase)) -> desc.toHardware(rs1, rs2)
      }
    }.unzip
    val out = Wire(Valid(chiselTypeOf(ctrl.head)))
    out.valid := Cat(valid).orR
    out.bits := Mux1H(valid, ctrl)
    out
  }

  // Scratchpad read (dedicated compute-timer copy)
  io.sp_read := select(bankGet(computeTimerBank, nPlans), computeFlags, executionPlans.map(_.sp_read))
  when(io.sp_read.fire) {
    // next row
    rs1.addr := (rs1.addr.zext + rs1.stride).asUInt
  }

  // Accum RAM read (dedicated accum-timer copy)
  io.acc_read := select(bankGet(accumTimerBank, nPlans), accumFlags, executionPlans.map(_.acc_read), Some(executionPlans.map(_.accStartCycle)))
  when(io.acc_read.fire) {
    rs2.addr := (rs2.addr.zext + rs2.stride).asUInt
  }

  // CMP Control (dedicated compute-timer copy)
  io.cmp_ctrl := select(bankGet(computeTimerBank, nPlans + 1), computeFlags, executionPlans.map(_.cmp_ctrl))

  // ACCUMULATOR Control (dedicated accum-timer copy); the per-lane Vec
  // elements all get the same value here -- the true fanout split happens
  // in MatrixEngineController where each element gets its own register copy
  val acc_ctrl_sel = select(bankGet(accumTimerBank, nPlans + 1), accumFlags, executionPlans.map(_.acc_ctrl), Some(executionPlans.map(_.accStartCycle)))
  io.acc_ctrl.foreach(_ := acc_ctrl_sel)

  // Release Semaphore (dedicated accum-timer copy for the release compares)
  io.sem_release.valid := Cat(computeFlags.zip(accumFlags).zip(executionPlans).zipWithIndex.map{ case (((cf, af), plan), i) =>
    (plan.sem_write.cycle > 0).B && (if (plan.useAccTimer(plan.sem_write.cycle)) {
      af && plan.sem_write.valid(bankGet(accumTimerBank, nPlans + 2), base = plan.accStartCycle) && header.releaseValid
    } else {
      cf && plan.sem_write.valid(bankGet(computeTimerBank, i)) && header.releaseValid
    })
  }).orR
  io.sem_release.bits.id := header.semId
  io.sem_release.bits.value := header.releaseSemValue

  // Update flags / timers (per-plan timer copies for the comparisons)
  val (computeDone, conflictFree, accumDone) = computeFlags.zip(accumFlags).zip(executionPlans).zipWithIndex.map{ case (((cf, af), plan), i) =>
    val cDone = cf && plan.computeDone(bankGet(computeTimerBank, i))
    val aDone = af && plan.accumDone(bankGet(accumTimerBank, i))
    val aStart = cf && {
      if (plan.accStartCycle > 0){ bankGet(computeTimerBank, i) === (plan.accStartCycle - 1).U }
      else false.B
    }
    when(cDone) {
      cf := false.B
    }
    when(aStart) {
      af := true.B
    }
    when(aDone) { af := false.B }
    val conflictFree = if (plan.useAccTimer(plan.conflict_free.cycle)) {
      af && plan.conflict_free.valid(bankGet(accumTimerBank, i), base = plan.accStartCycle)
    } else {
      cf && plan.conflict_free.valid(bankGet(computeTimerBank, i))
    }
    (cDone, conflictFree, aDone)
  }.unzip3

  // identical update logic applied to every copy of the bank, so all
  // copies stay value-locked (this is what keeps the split delay-neutral)
  computeTimerBank.foreach { t =>
    when(Cat(computeDone).orR) {
      t := 0.U
    }.elsewhen(Cat(computeFlags).orR){
      t := t + 1.U
    }
  }

  accumTimerBank.foreach { t =>
    when(Cat(accumDone).orR) {
      t := 0.U
    }.elsewhen(Cat(accumFlags).orR) {
      t := t + 1.U
    }
  }

  when(Cat(conflictFree).orR) {
    conflictFreeFlag.write(0, true.B)
  }

  when(io.in.fire) {
    val set_cf = computeFlags.zip(accumFlags).zip(executionPlans).zip(planFunc).map{ case (((cf, af), plan), func) =>
      val sel = func === io.in.bits.header.func
      if (plan.computeMaxCycle > 0) {
        cf := sel
      }
      if (plan.accumulateMaxCycle > 0 && plan.accStartCycle == 0) {
        af := sel
      }
      (plan.conflict_free.cycle == 0).B
    }
    conflictFreeFlag.write(1, Cat(set_cf).orR)
  }

  io.busy := computeValid || accumValid
  io.in.ready := !io.busy
  io.conflictFree := conflictFreeFlag.read(1)

  DelayedAssert(PopCount(computeFlags) <= 1.U)
  DelayedAssert(PopCount(accumFlags) <= 1.U)
}

/*
Pass `ArithmeticImpl` in because some execution plans may vary on it
*/
class MatrixEngineController[E <: Data : Arithmetic, A <: Data : Arithmetic](
  impl: ArithmeticImpl[E, A]
)(implicit p: Parameters) extends FSAModule {
  val io = IO(new MatrixControllerIO())

  /*
    we allow two instructions to be overlapped, they can control different
    part of the systolic array simultaneously
   */
  val fsm_list = (0 until 2) map { _ =>
    val (planFunc, allPlans) = fsaParams.supportedExecutionPlans(SA_ROWS, SA_COLS, impl).unzip
    val fsm = Module(new MatrixControlFSM(planFunc, allPlans))
    fsm
  }

  def Mux1HValidIO[T <: Data](in: Seq[Valid[T]], out: Valid[T]): Unit = {
    DelayedAssert(PopCount(in.map(_.valid)) <= 1.U)
    out.valid := Cat(in.map(_.valid)).orR
    out.bits := Mux1H(in.map(_.valid), in.map(_.bits))
  }

  val fsm_io = VecInit(fsm_list.map(_.io))

  val sp_read_all = Wire(chiselTypeOf(io.sp_read))
  val acc_read_all = Wire(chiselTypeOf(io.acc_read))
  val cmp_ctrl_all = Wire(chiselTypeOf(io.cmp_ctrl))
  val sem_release_all = Wire(chiselTypeOf(io.sem_release))
  val pe_ctrl_all = Wire(chiselTypeOf(io.pe_ctrl))

  Mux1HValidIO(fsm_io.map(_.sp_read), sp_read_all)
  Mux1HValidIO(fsm_io.map(_.acc_read), acc_read_all)
  Mux1HValidIO(fsm_io.map(_.cmp_ctrl), cmp_ctrl_all)
  Mux1HValidIO(fsm_io.map(_.sem_release), sem_release_all)
  // accumulator control is muxed PER LANE: each lane's decode cone reads
  // its own copy downstream instead of sharing one broadcast net
  val acc_ctrl_mux = Seq.fill(SA_COLS)(Wire(Valid(new AccumulatorControl)))
  for (j <- 0 until SA_COLS) {
    Mux1HValidIO(fsm_io.map(_.acc_ctrl(j)), acc_ctrl_mux(j))
  }
  pe_ctrl_all.zipWithIndex.foreach { case (out, idx) =>
    out.valid := Cat(fsm_io.map(_.pe_ctrl(idx).valid)).orR
    out.bits := fsm_io.map(_.pe_ctrl(idx).bits.asUInt).reduce(_ | _).asTypeOf(out.bits)
    // different control signals should not conflict
    DelayedAssert(!out.valid || fsm_io.map(_.pe_ctrl(idx).bits.asUInt).reduce(_ & _) === 0.U)
  }

  // Pipeline the control outputs to break the long combinational path from the
  // execution-plan timers (select / Mux1H logic) to the datapath (PEs,
  // accumulators, SRAM address ports).  Every consumer of these outputs sees
  // the same wavefront delayed by exactly one cycle, so the relative alignment
  // between SRAM read data and datapath control is preserved and no
  // execution-plan cycle counts need to change.
  io.sp_read := Pipe(sp_read_all)
  io.acc_read := Pipe(acc_read_all)
  io.cmp_ctrl := Pipe(cmp_ctrl_all)
  // Fanout split for the accumulator-control broadcast (M1 §4.0): SA_COLS
  // independent pipe registers, ALL loaded with the same value on the same
  // cycle (delay-neutral -- same +1 latency as the other piped outputs, so
  // schedule alignment is unchanged).  Each lane's ~63 decode points read
  // their own register instead of one register/LUT feeding ~1000 loads.
  // dontTouch prevents synthesis from merging the equivalent copies back
  // into a single register (which would recreate the 1007-fanout net).
  if (FanoutSplit.enabled) {
    io.acc_ctrl.zip(acc_ctrl_mux).foreach { case (out, m) =>
      val rep = Pipe(m)
      dontTouch(rep)
      out := rep
    }
  } else {
    // single shared pipe register fanning to all lanes (p3-equivalent)
    val rep = Pipe(acc_ctrl_mux.head)
    io.acc_ctrl.foreach(_ := rep)
  }
  io.sem_release := Pipe(sem_release_all)
  io.pe_ctrl := VecInit(pe_ctrl_all.map(Pipe(_)))

  val enq_ptr = RegInit(0.U(1.W))
  val deq_ptr = enq_ptr + 1.U
  when(io.in.fire) {
    enq_ptr := deq_ptr
  }

  // Only trust io.in.bits.header.waitPrevAcc when an instruction is actually
  // being presented (io.in.valid): the mxInst queue's bits registers have no
  // reset value, so reading them while the queue is EMPTY samples
  // uninitialized state.  That garbage fed this mux select and made
  // io.in.ready (and thus the mxBubble perf counter) depend on simulator
  // random-init draws -- i.e. on build structure, not on design logic.
  // Functionally benign (nothing enqueues while valid=0), but it made
  // delay-neutral RTL changes look like behavior changes.
  // Debug mode serializes the changed wavefront through accumulator drain.
  // The default candidate uses the verified row/column overlap boundaries.
  val canEnq = if (impl.peLatency > 0 && PipelineSchedule.conservative) !io.busy else Mux(io.in.valid && io.in.bits.header.waitPrevAcc,
    !io.busy,
    !fsm_io(deq_ptr).busy || fsm_io(deq_ptr).conflictFree
  )
  io.in.ready := fsm_io(enq_ptr).in.ready && canEnq
  fsm_io.map(_.in).zipWithIndex.foreach{ case (in, idx) =>
    in.valid := io.in.valid && idx.U === enq_ptr && canEnq
    in.bits := io.in.bits
  }

  io.busy := Cat(fsm_io.map(_.busy)).orR
}
