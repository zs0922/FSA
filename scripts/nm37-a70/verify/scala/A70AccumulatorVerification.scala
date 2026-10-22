package fsa

import chisel3._
import chisel3.util._
import chisel3.simulator.{EphemeralSimulator, PeekPokeAPI}
import _root_.circt.stage.ChiselStage
import easyfloat._
import fsa.arithmetic._
import scala.util.Random

/** The gold arithmetic is frozen pre-P3 source, not the refactored candidate with registers disabled. */
class A70AccumulatorMiter extends Module {
  val io = IO(new Bundle {
    val a = Input(UInt(32.W)); val b = Input(UInt(32.W)); val c = Input(UInt(32.W))
    val exp2 = Input(Bool())
    val gold = Output(UInt(32.W)); val gate = Output(UInt(32.W)); val mismatch = Output(Bool())
  })
  val constants = PyFPConst.slopes(8, 23, projectDir="generators/easyfloat", pieces=8)
    .zip(PyFPConst.intercepts(8, 23, projectDir="generators/easyfloat", pieces=8))
  val gold = Module(new A70LegacyRawFloat_MulAddExp2(9,24,9,24,Left(constants)))
  gold.io.in_a.fromIEEE(io.a,8,23); gold.io.in_b.fromIEEE(io.b,8,23); gold.io.in_c.fromIEEE(io.c,8,23)
  gold.io.in_exp2 := io.exp2
  val gate = Module(new FPAccUnit(8,23,8))
  gate.io.in_a := io.a.asTypeOf(gate.accType); gate.io.in_b := io.b.asTypeOf(gate.accType)
  gate.io.in_c := io.c.asTypeOf(gate.accType); gate.io.in_cmd := io.exp2.asUInt
  gate.multiCycleIO.reciprocal_in_valid := false.B
  io.gold := Rounding.round(gold.io.out,RoundingMode.RNE,8,23)
  io.gate := gate.io.out_accType.asUInt
  val expected = ShiftRegister(io.gold,3)
  val valid = ShiftRegister(!reset.asBool,3,false.B,true.B)
  io.mismatch := valid && io.gate =/= expected
}

/** Both feedback loops use the actual corresponding FMA and rounding; input is held until completion. */
class A70ReciprocalHarness extends Module {
  val io = IO(new Bundle {
    val in = Input(UInt(32.W)); val goldStart = Input(Bool()); val gateStart = Input(Bool())
    val gold = Output(UInt(32.W)); val gate = Output(UInt(32.W))
    val goldValid = Output(Bool()); val gateValid = Output(Bool())
  })
  val legacy = Module(new A70LegacyReciprocal(8,23))
  val fma = Module(new A70LegacyRawFloat_FMA(9,24,9,24,9,24))
  legacy.io.in := io.in; legacy.io.in_valid := io.goldStart
  fma.io.a := legacy.io.fma_rawA; fma.io.b := legacy.io.fma_rawB; fma.io.c := legacy.io.fma_rawC
  legacy.io.fma_rounded_result := Rounding.round(fma.io.out,RoundingMode.RNE,8,23)
  io.gold := legacy.io.out.bits; io.goldValid := legacy.io.out.valid
  val gate = Module(new FPAccUnit(8,23,8))
  gate.io.in_a := io.in.asTypeOf(gate.accType)
  gate.io.in_b := 0.U.asTypeOf(gate.accType); gate.io.in_c := 0.U.asTypeOf(gate.accType)
  gate.io.in_cmd := MacCMD.MAC; gate.multiCycleIO.reciprocal_in_valid := io.gateStart
  io.gate := gate.io.out_accType.asUInt; io.gateValid := gate.multiCycleIO.reciprocal_out_valid
}

object A70AccumulatorVerification extends PeekPokeAPI {
  def main(args: Array[String]): Unit = {
    if (args.headOption.contains("emit")) {
      ChiselStage.emitSystemVerilogFile(new A70AccumulatorMiter,
        Array("--target-dir",args(1)),
        Array("--disable-all-randomization","--strip-debug-info","--lowering-options=disallowLocalVariables,disallowPackedArrays"))
      return
    }
    val rnd = new Random(0xa703)
    val special = Seq(0L,0x80000000L,1L,0x007fffffL,0x00800000L,0x7f7fffffL,
      0x3f800000L,0xbf800000L,0x7f800000L,0xff800000L,0x7f800001L,0x7fc00000L,0xffffffffL)
    var checks = 0
    EphemeralSimulator.simulate(new A70AccumulatorMiter) { c =>
      c.io.a.poke(0);c.io.b.poke(0);c.io.c.poke(0);c.io.exp2.poke(false)
      c.reset.poke(true);c.clock.step(4);c.reset.poke(false)
      val expected = scala.collection.mutable.Queue[BigInt]()
      def cycle(a:Long,b:Long,d:Long,exp:Boolean):Unit = {
        c.io.a.poke(a);c.io.b.poke(b);c.io.c.poke(d);c.io.exp2.poke(exp)
        expected.enqueue(c.io.gold.peek().litValue)
        c.clock.step()
        if(expected.size >= 3){ c.io.gate.expect(expected.dequeue());checks += 1 }
      }
      for(a<-special;b<-special;d<-special;exp<-Seq(false,true))cycle(a,b,d,exp)
      for(_<-0 until 20000)cycle(rnd.nextLong()&0xffffffffL,rnd.nextLong()&0xffffffffL,rnd.nextLong()&0xffffffffL,rnd.nextBoolean())
      cycle(0,0,0,false);cycle(0,0,0,false)
    }
    println(s"PASS: A70 P3 accumulator continuous 3-cycle equivalence $checks outputs")
    var reciprocals = 0
    EphemeralSimulator.simulate(new A70ReciprocalHarness) { c =>
      c.io.in.poke(0);c.io.goldStart.poke(false);c.io.gateStart.poke(false)
      c.reset.poke(true);c.clock.step(4);c.reset.poke(false)
      val values = special ++ Seq.fill(512)(rnd.nextLong()&0xffffffffL)
      for(value<-values){
        c.io.in.poke(value);c.io.goldStart.poke(true);c.io.gateStart.poke(true)
        var golden:Option[BigInt]=None;var candidate:Option[BigInt]=None
        for(t<-0 until 76){
          val gv=c.io.goldValid.peek().litValue==1;val cv=c.io.gateValid.peek().litValue==1
          assert(gv==(t==18),s"legacy reciprocal completion cycle $t input $value")
          assert(cv==(t==75),s"P3 reciprocal completion cycle $t input $value")
          if(gv)golden=Some(c.io.gold.peek().litValue)
          if(cv)candidate=Some(c.io.gate.peek().litValue)
          c.clock.step()
          if(gv)c.io.goldStart.poke(false)
        }
        assert(golden==candidate,s"reciprocal mismatch input=$value gold=$golden gate=$candidate")
        c.io.gateStart.poke(false)
        // Allow zero or varying idle cycles; back-to-back next transactions are included.
        if (reciprocals % 4 != 0) c.clock.step(reciprocals % 4)
        reciprocals+=1
      }
    }
    println(s"PASS: A70 P3 reciprocal $reciprocals transactions; legacy 19 cycles, P3 76 cycles")
  }
}
