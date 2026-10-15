package easyfloat

import java.io.File
import scala.sys.process._
import scala.util.{Failure, Success, Try}
import chisel3._
import chisel3.util._

/**
  * in => shift | collect sticky bit => {in_shifted, sticky}
  */
class ShiftRightJam(val len: Int) extends Module {
  require(len > 0)
  val max_shift_width = log2Up(len + 1)
  val io = IO(new Bundle() {
    val in = Input(UInt(len.W))
    val shamt = Input(UInt())
    val out = Output(UInt(len.W))
    val sticky = Output(Bool())
  })
  val exceed_max_shift = io.shamt > len.U
  // Each combinational barrel stage collects exactly the bits it discards.
  // Avoid constructing a variable mask with a wide subtract-by-one chain.
  var shifted: UInt = io.in
  var sticky: Bool = false.B
  for (stage <- 0 until max_shift_width) {
    val distance = 1 << stage
    val discarded = shifted(math.min(distance, len) - 1, 0).orR
    sticky = sticky || (io.shamt(stage) && discarded)
    shifted = Mux(io.shamt(stage), shifted >> distance, shifted)
  }
  // Check the full shift amount: high bits must not wrap the barrel shift.
  io.out := Mux(exceed_max_shift, 0.U, shifted)
  io.sticky := Mux(exceed_max_shift, io.in.orR, sticky)
}

object ShiftRightJam {
  def apply(x: UInt, shamt: UInt): (UInt, Bool) = {
    val shiftRightJam = Module(new ShiftRightJam(x.getWidth))
    shiftRightJam.io.in := x
    shiftRightJam.io.shamt := shamt
    (shiftRightJam.io.out, shiftRightJam.io.sticky)
  }
}

object PyFPConst {

  /*
  projectDir: when running easyfloat in standalone mode, it's '.'
              but when running it as a submodule in chipyard,
              the project dir should be changed to 'generators/easyfloat'
  */
  private def runScript(choice: String, ew: Int, mw: Int, projectDir: String = ".", otherArgs: Seq[String]= Nil): String = {
    val scriptPath = new File(projectDir, "fp_consts.py").getAbsolutePath
    val command = Seq(
      "uv", "run", "--project", projectDir, scriptPath,
      choice, "--ew", ew.toString, "--mw", mw.toString
    ) ++ otherArgs
    val output: Try[String] = Try(command.!!)
    output match {
      case Failure(exception) =>
        sys.error(exception.getMessage)
      case Success(stdout) =>
        stdout
    }
  }

  def attentionScale(ew: Int, mw: Int, dk: Int, projectDir: String = "."): BigInt = {
    val out = runScript("attentionScale", ew, mw, projectDir, Seq("--dk", dk.toString)).strip()
    BigInt(out, 16)
  }

  private def pwl(choice: String)(ew: Int, mw: Int, projectDir: String = ".", pieces: Int = 8): Seq[BigInt] = {
    runScript(choice, ew, mw, projectDir, Seq("--pwl-pieces", pieces.toString)).split("\n").map(s =>
      BigInt(s, 16)
    )
  }

  def slopes(ew: Int, mw: Int, projectDir: String = ".", pieces: Int = 8) : Seq[BigInt] =
    pwl("slopes")(ew, mw, projectDir, pieces)

  def intercepts(ew: Int, mw: Int, projectDir: String = ".", pieces: Int = 8) : Seq[BigInt] =
    pwl("intercepts")(ew, mw, projectDir, pieces)
}
