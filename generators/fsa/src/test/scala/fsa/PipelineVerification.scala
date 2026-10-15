package fsa

import chisel3._
import chisel3.util._
import chisel3.simulator.{EphemeralSimulator, PeekPokeAPI}
import _root_.circt.stage.ChiselStage
import easyfloat._
import fsa.arithmetic._
import fsa.sa._
import scala.util.Random

/** Independent simultaneous legacy/candidate comparison; IEEE bit patterns are unrestricted. */
class PipelineMacMiter extends Module {
  val io = IO(new Bundle {
    val a = Input(UInt(16.W)); val b = Input(UInt(16.W)); val c = Input(UInt(32.W))
    val exp2 = Input(Bool())
    val mismatch = Output(Bool())
    val goldAcc = Output(UInt(32.W)); val gateAcc = Output(UInt(32.W))
    val goldElem = Output(UInt(16.W)); val gateElem = Output(UInt(16.W))
    val goldHit = Output(Bool()); val gateHit = Output(Bool())
  })
  val gold = Module(new FPMacUnit(5, 10, 8, 23, 8, afterAdd = false))
  val gate = Module(new FPMacUnit(5, 10, 8, 23, 8, afterAdd = true))
  for (unit <- Seq(gold, gate)) {
    unit.io.in_a := io.a.asTypeOf(unit.elemType)
    unit.io.in_b := io.b.asTypeOf(unit.elemType)
    unit.io.in_c := io.c.asTypeOf(unit.accType)
    unit.io.in_cmd := io.exp2.asUInt
  }
  io.goldAcc := gold.io.out_accType.asUInt; io.gateAcc := gate.io.out_accType.asUInt
  io.goldElem := gold.io.out_elemType.asUInt; io.gateElem := gate.io.out_elemType.asUInt
  io.goldHit := gold.io.out_exp2; io.gateHit := gate.io.out_exp2
  val expectedAcc = RegNext(io.goldAcc)
  val expectedElem = RegNext(io.goldElem)
  val expectedHit = RegNext(io.goldHit)
  val valid = RegNext(!reset.asBool, false.B)
  io.mismatch := valid && (expectedAcc =/= io.gateAcc || expectedElem =/= io.gateElem || expectedHit =/= io.gateHit)
}

class PipelineCmpMiter extends Module {
  val io = IO(new Bundle {
    val a = Input(UInt(32.W)); val b = Input(UInt(32.W))
    val mismatch = Output(Bool())
    val goldMax = Output(UInt(32.W)); val gateMax = Output(UInt(32.W))
    val goldDiff = Output(UInt(32.W)); val gateDiff = Output(UInt(32.W))
  })
  val gold = Module(new FPCmpUnit(8, 23, afterAdd = false))
  val gate = Module(new FPCmpUnit(8, 23, afterAdd = true))
  for (unit <- Seq(gold, gate)) {
    unit.io.in_a := io.a.asTypeOf(unit.accType)
    unit.io.in_b := io.b.asTypeOf(unit.accType)
  }
  io.goldMax := gold.io.out_max.asUInt; io.gateMax := gate.io.out_max.asUInt
  io.goldDiff := gold.io.out_diff.asUInt; io.gateDiff := gate.io.out_diff.asUInt
  val expectedDiff = RegNext(io.goldDiff)
  val valid = RegNext(!reset.asBool, false.B)
  io.mismatch := (io.goldMax =/= io.gateMax) || (valid && expectedDiff =/= io.gateDiff)
}

class PipelinePEHarness extends Module {
  val ev = new FPArithmeticImpl(5, 10, 8, 23, 8, pePipeline = true)
  val io = IO(new Bundle {
    val valid = Input(Bool()); val ctrl = Input(UInt(9.W))
    val left = Input(UInt(16.W)); val up = Input(UInt(32.W)); val down = Input(UInt(32.W))
    val oracleA = Input(UInt(16.W))
    val resident = Output(UInt(16.W)); val downOut = Output(UInt(32.W)); val downValid = Output(Bool())
    val upOut = Output(UInt(32.W)); val upValid = Output(Bool())
    val oracleElem = Output(UInt(16.W)); val oracleAcc = Output(UInt(32.W)); val oracleHit = Output(Bool())
    val slopes = Output(Vec(8, UInt(16.W))); val intercepts = Output(Vec(8, UInt(32.W)))
  })
  val pe = Module(new PE(ev))
  pe.io.in_ctrl.valid := io.valid
  for ((bit, i) <- pe.io.in_ctrl.bits.getCtrlElements.zipWithIndex) bit := io.ctrl(i)
  pe.io.l_input.valid := io.valid; pe.io.l_input.bits := io.left.asTypeOf(ev.elemType)
  pe.io.u_input.valid := io.valid; pe.io.u_input.bits := io.up.asTypeOf(ev.accType)
  pe.io.d_input.valid := io.valid; pe.io.d_input.bits := io.down.asTypeOf(ev.accType)
  io.resident := pe.io.r_output.bits.asUInt
  io.downOut := pe.io.d_output.bits.asUInt; io.downValid := pe.io.d_output.valid
  io.upOut := pe.io.u_output.bits.asUInt; io.upValid := pe.io.u_output.valid
  val oracle = Module(new FPMacUnit(5, 10, 8, 23, 8, afterAdd = false))
  oracle.io.in_a := io.oracleA.asTypeOf(ev.elemType)
  oracle.io.in_b := io.left.asTypeOf(ev.elemType)
  oracle.io.in_c := Mux(io.ctrl(1), io.up, io.down).asTypeOf(ev.accType)
  oracle.io.in_cmd := io.ctrl(8)
  io.oracleElem := oracle.io.out_elemType.asUInt
  io.oracleAcc := oracle.io.out_accType.asUInt
  io.oracleHit := oracle.io.out_exp2
  io.slopes := VecInit(ev.exp2PwlSlopes.map(_.asUInt))
  io.intercepts := VecInit(ev.exp2PwlIntercepts.map(_.asUInt))
}

/** Pure comparison-sign proof: dead subtraction output logic is removed by Yosys. */
class PipelineCmpMaxMiter extends Module {
  val io = IO(new Bundle { val a = Input(UInt(32.W)); val b = Input(UInt(32.W)); val mismatch = Output(Bool()) })
  val gold = Module(new FPCmpUnit(8, 23, afterAdd = false))
  val gate = Module(new FPCmpUnit(8, 23, afterAdd = true))
  for (unit <- Seq(gold, gate)) {
    unit.io.in_a := io.a.asTypeOf(unit.accType); unit.io.in_b := io.b.asTypeOf(unit.accType)
  }
  io.mismatch := gold.io.out_max.asUInt =/= gate.io.out_max.asUInt
}

object PipelineVerification extends PeekPokeAPI {
  def main(args: Array[String]): Unit = {
    if (args.headOption.contains("emit")) {
      val out = args(1)
      val options = Array("--disable-all-randomization", "--strip-debug-info", "--lowering-options=disallowLocalVariables")
      ChiselStage.emitSystemVerilogFile(new PipelineMacMiter, Array("--target-dir", out + "/mac"), options)
      ChiselStage.emitSystemVerilogFile(new PipelineCmpMiter, Array("--target-dir", out + "/cmp"), options)
      ChiselStage.emitSystemVerilogFile(new PipelineCmpMaxMiter, Array("--target-dir", out + "/cmpmax"), options)
      return
    }
    val random = new Random(0x51a7)
    val half = Seq(0x0000, 0x8000, 0x0001, 0x03ff, 0x0400, 0x7bff, 0x3c00, 0xbc00, 0x7c00, 0xfc00, 0x7c01, 0x7e00, 0xffff)
    val single = Seq(0L, 0x80000000L, 1L, 0x007fffffL, 0x00800000L, 0x7f7fffffL, 0x3f800000L, 0xbf800000L, 0x7f800000L, 0xff800000L, 0x7f800001L, 0x7fc00000L, 0xffffffffL)
    var macCount = 0
    EphemeralSimulator.simulate(new PipelineMacMiter) { c =>
      c.reset.poke(true); c.clock.step(); c.reset.poke(false)
      def check(a: Int, b: Int, d: Long, exp: Boolean): Unit = {
        c.io.a.poke(a); c.io.b.poke(b); c.io.c.poke(d); c.io.exp2.poke(exp)
        val acc = c.io.goldAcc.peek().litValue
        val elem = c.io.goldElem.peek().litValue
        val hit = c.io.goldHit.peek().litValue
        c.clock.step()
        c.io.gateAcc.expect(acc); c.io.gateElem.expect(elem); c.io.gateHit.expect(hit)
        macCount += 1
      }
      for (a <- half; b <- half; d <- single; exp <- Seq(false, true)) check(a, b, d, exp)
      for (_ <- 0 until 10000) check(random.nextInt(65536), random.nextInt(65536), random.nextLong() & 0xffffffffL, random.nextBoolean())
    }
    var cmpCount = 0
    EphemeralSimulator.simulate(new PipelineCmpMiter) { c =>
      c.reset.poke(true); c.clock.step(); c.reset.poke(false)
      def check(a: Long, b: Long): Unit = {
        c.io.a.poke(a); c.io.b.poke(b)
        c.io.gateMax.expect(c.io.goldMax.peek().litValue)
        val diff = c.io.goldDiff.peek().litValue
        c.clock.step(); c.io.gateDiff.expect(diff)
        cmpCount += 1
      }
      for (a <- single; b <- single) check(a, b)
      // Adjacent magnitudes, sign, FTZ, infinities and NaNs are all represented.
      for (_ <- 0 until 20000) check(random.nextLong() & 0xffffffffL, random.nextLong() & 0xffffffffL)
    }
    println(s"PASS: FPMac delay equivalence $macCount cases; legacy CMP max/diff equivalence $cmpCount cases.")
    var scans = 0
    EphemeralSimulator.simulate(new PipelinePEHarness) { c =>
      c.io.valid.poke(false); c.io.ctrl.poke(0); c.io.left.poke(0); c.io.up.poke(0); c.io.down.poke(0); c.io.oracleA.poke(0)
      c.reset.poke(true); c.clock.step(2); c.reset.poke(false)
      val slopes = (0 until 8).map(i => c.io.slopes(i).peek().litValue)
      val intercepts = (0 until 8).map(i => c.io.intercepts(i).peek().litValue)
      def bubble(n: Int): Unit = { c.io.valid.poke(false); c.clock.step(n) }
      def readResident(expected: BigInt): Unit = { c.io.valid.poke(false); c.io.ctrl.poke(1 << 2); c.io.resident.expect(expected) }
      // Include all PWL boundaries, large negative values, both zeros, and NaN/Inf.
      val inputs = Seq(0x0000, 0x8000, 0xb000, 0xb400, 0xb600, 0xb800, 0xb900, 0xba00, 0xbb00, 0xbc00, 0xc800, 0xcc00, 0x7c00, 0xfc00, 0x7e00) ++ Seq.fill(30)(random.nextInt(65536))
      for (a <- inputs; gaps <- Seq(false, true)) {
        c.io.ctrl.poke(1 << 2); c.io.valid.poke(true); c.io.left.poke(a); c.clock.step()
        c.io.oracleA.poke(a)
        var expected: Option[BigInt] = None
        for (i <- 0 until 8) {
          c.io.ctrl.poke((1 << 8) | (1 << 1)); c.io.valid.poke(true)
          c.io.left.poke(slopes(i)); c.io.up.poke(intercepts(i))
          if (c.io.oracleHit.peek().litValue == 1) expected = Some(c.io.oracleElem.peek().litValue)
          c.clock.step()
          if (gaps && i != 7) bubble(1 + random.nextInt(2))
        }
        // Drain the delayed result and resident writeback; the next instruction is MAC.
        bubble(1)
        readResident(expected.getOrElse(BigInt(a)))
        c.io.ctrl.poke((1 << 0) | (1 << 1) | (1 << 7)); c.io.valid.poke(true)
        c.io.left.poke(0x3c00); c.io.up.poke(0); c.io.oracleA.poke(expected.getOrElse(BigInt(a)))
        val updated = c.io.oracleElem.peek().litValue
        val acc = c.io.oracleAcc.peek().litValue
        c.clock.step(); c.io.downValid.expect(1); c.io.downOut.expect(acc)
        bubble(1); readResident(updated)
        // Switching direction and bypass must preserve bits, including arbitrary tags.
        c.io.ctrl.poke(1 << 6); c.io.valid.poke(true); c.io.down.poke(0xa5a5f00dL)
        c.clock.step(); c.io.upValid.expect(1); c.io.upOut.expect(0xa5a5f00dL)
        bubble(1); c.io.upValid.expect(0)
        scans += 1
      }
    }
    println(s"PASS: PE EXP2 scans $scans cases (continuous/gapped, all segments, EXP2/MAC/bypass switches).")
  }
}
