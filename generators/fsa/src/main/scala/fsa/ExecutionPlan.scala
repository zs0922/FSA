package fsa

import collection.mutable.ListBuffer
import chisel3._
import chisel3.util._
import fsa.arithmetic.HasArithmeticParams
import fsa.isa._
import fsa.sa._
import fsa.utils.UIntRangeHelper._
import org.chipsalliance.cde.config.Parameters

trait CanGenerateHw[T <: Data] {
  def toHardware(rs1: MatrixInstructionSpad, rs2: MatrixInstructionAcc)(implicit p: Parameters): T
}

trait HasEffRange {
  val cycle: Int
  val repeat: Int

  def valid(t: UInt, base: Int = 0): Bool = {
    require(repeat >= 1)
    if (cycle < 0) false.B else t.between(cycle - base, cycle + repeat - base)
  }
}

trait ExecutionPlan {
  val rows: Int
  val cols: Int
  // pipelined accumulator FMA latency; plans that touch the accumulator must
  // account for it so that drain/writeback stays inside the busy window
  def accLatency: Int = 0

  // ===================== Wavefront geometry landmarks (M0 §3.1, Phase A) =====================
  // PURE RENAME refactor: every landmark below evaluates to exactly the
  // historical hand-tuned literal.  All values verified against VCD
  // archaeology on 4x4/8x8/16x16 (execTime baselines 368/381/420; bracketed
  // [a/b/c] numbers are the observed plan-cycles at R=4/8/16).
  // The decomposition separates hop-count terms (rows/cols multiples -- the
  // terms M1 §4.1 rescales when the PE MAC gets pipelined) from fixed
  // pipeline gaps (the bare +1s).  Terms marked PROVISIONAL have a verified
  // total but an unverified per-hop split; PE-level archaeology pending.

  // per-hop latencies through the mesh (all 1 today: PE MAC is combinational
  // and every inter-PE link has exactly one pipe register, SystolicArray.pipe_no_reset)
  def hopLR: Int = 1  // K stream / ctrl chains, left -> right
  def hopUD: Int = 1  // partial-sum chain, up <-> down

  // K row i enters the mesh at cycle 1 + i (one spad row read per cycle)
  def kInject(i: Int): Int = 1 + i * hopLR
  def kInjectDone: Int = kInject(rows - 1)                            // = rows

  // PE control signals are more complex than spad/acc/cmp control, use a dedicate `ControlGen` to optimize them
  val pe_signals = (0 until 9).map(_ => ControlGen(rows)).toList
  val mac :: acc_ui :: load_reg_li :: load_reg_ui :: flow_lr :: flow_ud :: flow_du :: update_reg :: exp2 :: Nil = pe_signals

  def genPECtrl(timer: UInt, valid: Bool): Vec[PECtrl] = {
    val pe_ctrl = Wire(Vec(rows, new PECtrl))
    pe_signals
      .zip(pe_ctrl.map(_.getCtrlElements).transpose)
      .foreach { case (gen, ctrlBits) =>
        gen.generateCtrl(timer, valid).zip(ctrlBits).foreach {
          case (generated, connected) => connected := generated
        }
      }
    pe_ctrl
  }

  case class ConstRead(idx: Int, revIn: Boolean, revOut: Boolean, delay: Boolean)

  case class SpReadDesc(cycle: Int, repeat: Int, const: Option[ConstRead]) extends
    CanGenerateHw[SpRead] with HasEffRange
  {
    override def toHardware(rs1: MatrixInstructionSpad, rs2: MatrixInstructionAcc)(implicit p: Parameters): SpRead = {
      val r = Wire(new SpRead())
      r.rev_sram_out := rs1.revInput
      r.rev_delayer_out := rs1.revOutput
      r.delay_sram_out := rs1.delayOutput
      r.addr := rs1.addr
      r.is_constant := const.nonEmpty.B
      const.foreach { c =>
        r.rev_sram_out := c.revIn.B
        r.rev_delayer_out := c.revOut.B
        r.delay_sram_out := c.delay.B
        r.addr := c.idx.U
      }
      r
    }
  }

  case class AccReadDesc(cycle: Int, repeat: Int, const: Option[ConstRead], rmw: Boolean)
    extends CanGenerateHw[AccRead] with HasEffRange
  {
    override def toHardware(rs1: MatrixInstructionSpad, rs2: MatrixInstructionAcc)(implicit p: Parameters): AccRead = {
      val r = Wire(new AccRead())
      r.addr := rs2.addr
      r.is_constant := const.nonEmpty.B || rs2.zero
      r.const_idx := const.map(_.idx).getOrElse(AccConstIdx.ZERO).U
      r.rmw := rmw.B
      r
    }
  }

  case class CmpCtrlDesc(cycle: Int, repeat: Int, command: UInt)
    extends CanGenerateHw[CmpControl] with HasEffRange
  {
    override def toHardware(rs1: MatrixInstructionSpad, rs2: MatrixInstructionAcc)(implicit p: Parameters): CmpControl = {
      val ctrl = Wire(new CmpControl)
      ctrl.cmd := command
      ctrl
    }
  }

  case class AccCtrlDesc(cycle: Int, repeat: Int, command: UInt)
    extends CanGenerateHw[AccumulatorControl] with HasEffRange
  {
    override def toHardware(rs1: MatrixInstructionSpad, rs2: MatrixInstructionAcc)(implicit p: Parameters): AccumulatorControl = {
      val ctrl = Wire(new AccumulatorControl)
      ctrl.cmd := command
      ctrl
    }
  }

  def useAccTimer(cycle: Int): Boolean = {
    require(
      cycle < computeMaxCycle || cycle < accumulateMaxCycle,
      f"cycle $cycle must be less than computeMaxCycle $computeMaxCycle or accumulateMaxCycle $accumulateMaxCycle"
    )
    accumulateMaxCycle > 0 && cycle >= accStartCycle
  }

  val sp_read = ListBuffer[SpReadDesc]()
  val cmp_ctrl = ListBuffer[CmpCtrlDesc]()
  val acc_read = ListBuffer[AccReadDesc]()
  val acc_ctrl = ListBuffer[AccCtrlDesc]()

  var semaphoreReleaseCycle: Option[Int] = None
  var conflictFreeCycle: Option[Int] = None

  lazy val sem_write: HasEffRange = new HasEffRange {
    override val cycle: Int = semaphoreReleaseCycle.getOrElse(-1)
    override val repeat: Int = 1
  }
  lazy val conflict_free: HasEffRange = new HasEffRange {
    override val cycle: Int = conflictFreeCycle.get
    override val repeat: Int = 1
  }

  def readScratchPad(cycle: Int, repeat: Int, const: Option[ConstRead]) = {
    sp_read += SpReadDesc(cycle, repeat, const)
  }

  def readAccRAM(cycle: Int, repeat: Int, const: Option[ConstRead], rmw: Boolean = true) = {
    acc_read += AccReadDesc(cycle, repeat, const, rmw)
  }

  def setComparator(cycle: Int, repeat: Int, command: UInt) = {
    cmp_ctrl += CmpCtrlDesc(cycle, repeat, command)
  }

  def setAccumulator(cycle: Int, repeat: Int, command: UInt) = {
    acc_ctrl += AccCtrlDesc(cycle, repeat, command)
  }

  def releaseSemaphore(cycle: Int) = {
    require(semaphoreReleaseCycle.isEmpty)
    semaphoreReleaseCycle = Some(cycle)
  }

  def setConflictFree(cycle: Int) = {
    require(conflictFreeCycle.isEmpty)
    conflictFreeCycle = Some(cycle)
  }

  // exclusive
  def maxCycle(desc_list: Seq[HasEffRange]) = desc_list.map(d => d.cycle + d.repeat).maxOption.getOrElse(0)

  def computeMaxCycle = (
    Seq(sp_read, cmp_ctrl).map(x => maxCycle(x.toSeq)) ++ pe_signals.map(_.maxCycle)
  ).max

  // inclusive
  def accStartCycle = {
    Seq(acc_read, acc_ctrl).flatMap(_.map(_.cycle)).minOption.map { m =>
      Seq(m, computeMaxCycle).min
    }.getOrElse(-1)
  }

  // inclusive; + accLatency keeps the FSM busy until pipelined writebacks drain
  def accumulateMaxCycle = {
    val m = Seq(acc_read, acc_ctrl).map(x => maxCycle(x.toSeq)).max
    if (m > 0) m + accLatency else m
  }

  def computeDone(timer: UInt) = if (computeMaxCycle == 0) true.B else timer === (computeMaxCycle - 1).U

  def accumDone(timer: UInt) = {
    if (accumulateMaxCycle > 0) {
      timer === (accumulateMaxCycle - 1 - accStartCycle).U
    } else {
      false.B
    }
  }

}

class LoadStationary(val rows: Int, val cols: Int, spadLatency: Int = 1) extends ExecutionPlan {
  // read Q from spad
  readScratchPad(0, cols, None)
  // release the semaphore immediately at the last cycle of reading sram
  releaseSemaphore(cols - 1)
  // load into systolic array
  load_reg_li.parallel(spadLatency, cols)
  /*
    Although we would occupy pe control signals until cycle `cols`,
    the next instruction should always read sram first (with 1 cycle
    latency), so we can start the next instruction at cycle `cols-1`
   */
  setConflictFree(cols - 1)
}

class AttentionScoreExecPlan(val rows: Int, val cols: Int, ap: HasArithmeticParams) extends ExecutionPlan {
  override val accLatency: Int = ap.accLatency
  override def hopUD: Int = 1 + ap.peLatency

  if (ap.peLatency > 0) {
    PipelinedAttentionSchedule.score(this, ap)
  } else {

  // ---- landmark chain (sums of hop terms + fixed pipe gaps; see trait doc) ----
  // S drain (partial sums to the CMP row) starts right after the wavefront head
  val scoreUpdate = kInjectDone + hopUD                        // = R+1    [5/9/17]
  // row-max propagation: the UPDATE burst staggers across rows-1 (ControlGen
  // Downward skew), then one fixed pipe gap before phase 1
  val scorePhase1 = scoreUpdate + (rows - 1) * hopUD + 1       // = 2R+1   [9/17/33]
  val scorePhase2 = scorePhase1 + 1                            // = 2R+2   [10/18/34]
  val scorePhase3 = scorePhase2 + 1                            // = 2R+3   [11/19/35]
  val exp2Start = scorePhase3 + 1                              // = 2R+4
  val exp2Cycles = ap.exp2PwlPieces
  val exp2End = exp2Start + exp2Cycles - 1                     //          [19/27/43]
  // EXP_S1: the old_max-new_max diff reaches the accumulator lanes.
  // Composition: phase-1 wavefront + diagonal drain (rows+cols) + one pipe
  // gap (= 3R+C+2).  PROVISIONAL split of the (rows+cols) term.
  val expS1 = scorePhase1 + rows + cols + 1                    //          [18/34/66]
  val expS2 = expS1 + 1 + ap.accLatency                        //          [22/38/70]
  // old-sum read for the online-rescale ACC_SA: diagonal drain after exp2 end
  val rowSumRead = exp2End + rows + cols - 1
  val accSa = rowSumRead + 1                                   //          [27/43/75]
  // zero flow (bottom-up) pre-positions 0 partials for the exp-sum phase
  val zeroFlow = scoreUpdate + 3                               // = R+4

  /****** S = Q @ K ******/
  // read K from spad (row i enters the mesh at kInject(i) = 1+i)
  readScratchPad(0, rows, None)
  // release the semaphore immediately at the last cycle of reading sram
  releaseSemaphore(rows - 1)
  // stream in K, multiply with Q from bottom left of the SA
  mac.flow_up(kInject(0), rows)
  flow_lr.flow_up(kInject(0), rows)

  /****** Put S back to systolic array ******/
  // the operations above takes `rows` cycles (the first element of
  // S = Q @ Kt reaches the upper left of the SA),
  // so we need to wait for `rows` cycles before we can read S from the SA
  flow_ud.flow_down(scoreUpdate, rows)
  // meanwhile, update the row max
  setComparator(scoreUpdate, rows, CmpControlCmd.UPDATE)

  /****** Flow zero bottom-up for later exp sum ******/
  flow_du.flow_up(zeroFlow, rows)

  // prepare input for next cycle
  load_reg_ui.parallel(scorePhase1, 1)
  setComparator(scorePhase1, 1, CmpControlCmd.PROP_MAX)
  readScratchPad(
    scorePhase1, 1,
    Some(ConstRead(SpadConstIdx.ONE, revIn = false, revOut = false, delay = true))
  )
  /****** Staring from the first column, do element-wise ops ******/
  // s = s * 1 + (-m)
  update_reg.flow_down(scorePhase2, 1)
  acc_ui.flow_down(scorePhase2, 1)
  flow_ud.flow_down(scorePhase2, 1)
  flow_lr.flow_down(scorePhase2, 1)

  // prepare input for next cycle
  setComparator(scorePhase2, 1, CmpControlCmd.PROP_MAX_DIFF)
  readScratchPad(
    scorePhase2, 1,
    Some(ConstRead(SpadConstIdx.AttentionScale, revIn = false, revOut = false, delay = true))
  )
  // pass down delta_m; compute (s-m) * log2e in place
  flow_ud.flow_down(scorePhase3, 1)
  update_reg.flow_down(scorePhase3, 1)
  flow_lr.flow_down(scorePhase3, 1)

  setComparator(exp2Start - 1, exp2Cycles, CmpControlCmd.PROP_EXP2_INTERCEPTS)
  readScratchPad(
    exp2Start - 1, exp2Cycles,
    Some(ConstRead(SpadConstIdx.Exp2Slopes, revIn = false, revOut = false, delay = true))
  )
  // use pow2 to generate exp
  flow_ud.flow_down(exp2Start, exp2Cycles)
  flow_lr.flow_down(exp2Start, exp2Cycles)
  acc_ui.flow_down(exp2Start, exp2Cycles)
  exp2.flow_down(exp2Start, exp2Cycles)

  // prepare input for next cycle
  setComparator(exp2End, 1, CmpControlCmd.PROP_ZERO)
  readScratchPad(
    exp2End, 1,
    Some(ConstRead(SpadConstIdx.ONE, revIn = false, revOut = false, delay = true))
  )
  // use mac to compute the sum of exp
  mac.flow_down(exp2End + 1, 1)
  acc_ui.flow_down(exp2End + 1, 1)
  flow_lr.flow_down(exp2End + 1, 1)

  /*
    The next instruction should always be `S @ V`,
    start it at cycle exp2End so that the sram read
    of the next instruction can overlap with the last compute
    of the current instruction
  */
  setConflictFree(exp2End)

  // collect diff = row_max(i-1) - row_max(i), and compute exp(diff)
  setAccumulator(expS1, 1, AccumulatorCmd.EXP_S1)
  setAccumulator(expS2, 1, AccumulatorCmd.EXP_S2)
  // update exp sum
  readAccRAM(rowSumRead, 1, None)
  setAccumulator(accSa, 1, AccumulatorCmd.ACC_SA)
  }
}

class AttentionValueExecPlan(val rows: Int, val cols: Int, ap: HasArithmeticParams) extends ExecutionPlan {
  override val accLatency: Int = ap.accLatency
  override def hopUD: Int = 1 + ap.peLatency

  if (ap.peLatency > 0) {
    PipelinedAttentionSchedule.value(this, ap)
  } else {

  // ---- landmark chain (see trait doc; R+C terms are the DIAGONAL wavefront,
  // ---- distinct from the 2R drain terms -- do not conflate when rescaling hops)
  // old-O read: the O drain diagonal completes its last beat (NOT 2R-2: verified
  // [7/15/31] = R+C-1, distinguishable from 2R-2 only with R != C or VCD)
  val accReadOld = rows + cols - 1                             //          [7/15/31]
  val accCmd = accReadOld + 1                                  // = R+C    [9/17/33]
  val conflictFreeAt = 2 * rows - 2                            // compute drain done

  /****** O = P @ V ******/
  // read V from spad (row i enters the mesh at kInject(i) = 1+i)
  readScratchPad(0, rows, None)
  // release the semaphore immediately at the last cycle of reading sram
  releaseSemaphore(rows - 1)
  // V enters the SA from upper left
  mac.flow_down(kInject(0), rows)
  acc_ui.flow_down(kInject(0), rows)
  flow_lr.flow_down(kInject(0), rows)

  /*
    If the next instruction is load stationary (next inner loop start),
    the last compute of the current instructions happens at cycle `2*rows - 1`,
    we release at `2*rows - 2` to allow the sram read of the next instruction
    overlap with current compute
  */
  setConflictFree(conflictFreeAt)

  // read old O out from accumulator sram
  readAccRAM(accReadOld, rows, None)
  // accumulate, update O
  setAccumulator(accCmd, rows, AccumulatorCmd.ACC_SA)
  }
}

/** Plan-cycle geometry excludes the common controller output register.
  * Horizontal control/data advance one column per cycle. The vertical PE
  * result (including bypass) advances by d cycles. CMP still accepts a score
  * every cycle, so loading its stream requires a different row slope.
  */
object PipelineSchedule {
  // A debug comparison mode: whole-array phase barriers and no FSM overlap.
  val conservative: Boolean = sys.props.get("fsa.pipelineConservative") match {
    case None | Some("false") => false
    case Some("true") => true
    case Some(value) => throw new IllegalArgumentException(s"Invalid fsa.pipelineConservative=$value")
  }
}

case class PipelinedScoreTiming(rows: Int, cols: Int, ap: HasArithmeticParams,
                               conservative: Boolean = PipelineSchedule.conservative) {
  val d = 1 + ap.peLatency
  val dc = 1 + ap.cmpLatency
  val ls = ap.spadLatency
  val commitGap = ap.peLatency + 1
  val scoreUpdate = ls + rows * d
  def scoreLoad(row: Int): Int = scoreUpdate + rows - 1 + dc + row * (d - 1)
  // CMP max is committed after its last UPDATE. The first row's score load
  // and every later load are also complete before this downward wave reaches
  // them: load(r) has slope d-1, whereas subtract(r) has slope d.
  val sub = if (conservative) scoreLoad(rows - 1) + 1 + dc
            else math.max(scoreLoad(0) + 1, scoreUpdate + rows + dc)
  val phaseBarrier = if (conservative) (rows - 1) * d else 0
  val scale = sub + phaseBarrier + commitGap
  val exp = scale + phaseBarrier + commitGap
  val expLast = exp + (rows - 1) * d + ap.exp2PwlPieces - 1
  val sum = exp + phaseBarrier + ap.exp2PwlPieces - 1 + commitGap
  val zeroFlow = if (conservative) scoreUpdate + rows else ls + rows
  // OutputDelayer cancels the horizontal skew by adding cols-1-column.
  val diffAtAccumulator = scale + rows * d + cols - 1
  val sumAtAccumulator = sum + rows * d + cols - 1
}

object PipelinedAttentionSchedule {
  def score(plan: ExecutionPlan, ap: HasArithmeticParams): Unit = {
    val t = PipelinedScoreTiming(plan.rows, plan.cols, ap)
    import plan._
    readScratchPad(0, rows, None)
    releaseSemaphore(rows - 1)
    mac.flow_up(t.ls, rows, t.d)
    flow_lr.flow_up(t.ls, rows, t.d)
    setComparator(t.scoreUpdate, rows, CmpControlCmd.UPDATE)

    // Score beat j is emitted at U+j. Row r must retain j=R-1-r:
    // U+(R-1-r)+Dc+r*d, rather than a parallel row load.
    for (r <- 0 until rows) {
      val beatsToForward = rows - 1 - r
      if (beatsToForward > 0) {
        flow_ud.at_row(r, t.scoreUpdate + t.dc + r * t.d, beatsToForward)
      }
      load_reg_ui.at_row(r, t.scoreLoad(r), 1)
    }
    // Follow each row's final upward MAC by one cycle. Zero reaches CMP
    // after its UPDATE burst and is resident before scale reads d_input.
    flow_du.flow_up(t.zeroFlow, 1, t.d)

    def constant(at: Int, idx: Int, repeat: Int = 1): Unit =
      readScratchPad(at - t.ls, repeat,
        Some(ConstRead(idx, revIn = false, revOut = false, delay = true)))

    setComparator(t.sub - t.dc, 1, CmpControlCmd.PROP_MAX)
    constant(t.sub, SpadConstIdx.ONE)
    update_reg.flow_down(t.sub, 1, t.d)
    acc_ui.flow_down(t.sub, 1, t.d)
    flow_ud.flow_down(t.sub, 1, t.d)
    flow_lr.flow_down(t.sub, 1, t.d)

    setComparator(t.scale - t.dc, 1, CmpControlCmd.PROP_MAX_DIFF)
    constant(t.scale, SpadConstIdx.AttentionScale)
    flow_ud.flow_down(t.scale, 1, t.d)
    update_reg.flow_down(t.scale, 1, t.d)
    flow_lr.flow_down(t.scale, 1, t.d)

    setComparator(t.exp - t.dc, ap.exp2PwlPieces, CmpControlCmd.PROP_EXP2_INTERCEPTS)
    constant(t.exp, SpadConstIdx.Exp2Slopes, ap.exp2PwlPieces)
    flow_ud.flow_down(t.exp, ap.exp2PwlPieces, t.d)
    flow_lr.flow_down(t.exp, ap.exp2PwlPieces, t.d)
    acc_ui.flow_down(t.exp, ap.exp2PwlPieces, t.d)
    exp2.flow_down(t.exp, ap.exp2PwlPieces, t.d)

    setComparator(t.sum - t.dc, 1, CmpControlCmd.PROP_ZERO)
    constant(t.sum, SpadConstIdx.ONE)
    mac.flow_down(t.sum, 1, t.d)
    acc_ui.flow_down(t.sum, 1, t.d)
    flow_lr.flow_down(t.sum, 1, t.d)

    setAccumulator(t.diffAtAccumulator, 1, AccumulatorCmd.EXP_S1)
    setAccumulator(t.diffAtAccumulator + 1 + ap.accLatency, 1, AccumulatorCmd.EXP_S2)
    readAccRAM(t.sumAtAccumulator - 1, 1, None)
    setAccumulator(t.sumAtAccumulator, 1, AccumulatorCmd.ACC_SA)
    // The next value wave begins after the sum wave, at the same row/column
    // skew. Its first accumulator command follows this sum command by >=2
    // cycles, and EXP_S2 has committed before either ACC_SA command.
    setConflictFree(if (t.conservative) accumulateMaxCycle - 1 else t.sum - 1)
  }

  def value(plan: ExecutionPlan, ap: HasArithmeticParams): Unit = {
    import plan._
    val d = 1 + ap.peLatency
    val firstAccumulator = ap.spadLatency + rows * d + cols - 1
    readScratchPad(0, rows, None)
    releaseSemaphore(rows - 1)
    mac.flow_down(ap.spadLatency, rows, d)
    acc_ui.flow_down(ap.spadLatency, rows, d)
    flow_lr.flow_down(ap.spadLatency, rows, d)
    readAccRAM(firstAccumulator - 1, rows, None)
    setAccumulator(firstAccumulator, rows, AccumulatorCmd.ACC_SA)
    // A following stationary load arrives after the bottom row's last MAC;
    // both controls retain the same one-cycle horizontal column skew.
    setConflictFree(if (PipelineSchedule.conservative) accumulateMaxCycle - 1
      else ap.spadLatency + (rows - 1) * d + rows - 1 - ap.spadLatency)
  }
}

// load one row from AccRAM to accumulator and get the reciprocal
class AttentionLseNormScale
(
  val rows: Int,
  val cols: Int,
  ap: HasArithmeticParams
) extends ExecutionPlan {
  override val accLatency: Int = ap.accLatency
  readAccRAM(0, 1, None, rmw = false)
  setAccumulator(1, 1, AccumulatorCmd.SET_SCALE)
  // scale is registered accLatency cycles after SET_SCALE consumes sram_in,
  // so the reciprocal (which reads scale) must start accLatency cycles later
  setAccumulator(2 + ap.accLatency, ap.reciprocalLatency, AccumulatorCmd.RECIPROCAL)
  releaseSemaphore(2 + ap.accLatency + ap.reciprocalLatency - 1)
  // This is a blocking instruction
  setConflictFree(2 + ap.accLatency + ap.reciprocalLatency - 1)
}

// perform the final lse norm after each flash attention inner loop
class AttentionLseNorm(val rows: Int, val cols: Int, ap: HasArithmeticParams) extends ExecutionPlan {
  override val accLatency: Int = ap.accLatency
  setComparator(0, 1, CmpControlCmd.RESET)
  readAccRAM(0, rows, None)
  setAccumulator(1, rows, AccumulatorCmd.ACC)
  // pipelined writebacks land rows + accLatency cycles later
  releaseSemaphore(rows + ap.accLatency)
  // This is a blocking instruction
  setConflictFree(rows + ap.accLatency)
}
