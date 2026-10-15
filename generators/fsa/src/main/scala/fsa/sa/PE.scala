package fsa.sa

import chisel3._
import chisel3.util._
import chisel3.experimental.hierarchy._
import fsa.arithmetic._


class PECtrl extends Bundle {
  val mac = Bool()
  val acc_ui = Bool()
  val load_reg_li = Bool()
  val load_reg_ui = Bool()
  // pass through
  val flow_lr = Bool()
  val flow_ud = Bool()
  val flow_du = Bool()
  // mac_out -> reg
  val update_reg = Bool()
  // compute 2^reg
  val exp2 = Bool()

  // getElements might be dangerous, define them manually
  def getCtrlElements: Seq[Bool]= Seq(
    mac, acc_ui, load_reg_li, load_reg_ui,
    flow_lr, flow_ud, flow_du,
    update_reg, exp2
  )
}

@instantiable
class PE[E <: Data : Arithmetic, A <: Data : Arithmetic]
(ev: ArithmeticImpl[E, A]) extends Module
{
  val (accType, elemType, macGen) = (ev.accType, ev.elemType, ev.peMac _)
  @public val io = IO(new Bundle {
    val in_ctrl = Flipped(Valid(new PECtrl))
    val out_ctrl = Valid(new PECtrl)
    val u_input = Flipped(Valid(accType))
    val u_output = Valid(accType)
    val d_input = Flipped(Valid(accType))
    val d_output = Valid(accType)
    val l_input = Flipped(Valid(elemType))
    val r_output = Valid(elemType)
  })

  val macUnit = Module(macGen())

  val reg = Reg(elemType)
  val ctrl = io.in_ctrl.bits
  // TODO: this is actually useless, ctrl signals does not depend on fire
  val fire = io.in_ctrl.fire

  macUnit.io.in_a := reg
  macUnit.io.in_b := io.l_input.bits
  macUnit.io.in_c := Mux(ctrl.acc_ui, io.u_input.bits, io.d_input.bits)
  macUnit.io.in_cmd := Mux(ctrl.exp2, MacCMD.EXP2, MacCMD.MAC)

  io.out_ctrl := io.in_ctrl

  io.r_output.bits := Mux(ctrl.load_reg_li, reg, io.l_input.bits)
  io.r_output.valid := fire && (ctrl.load_reg_li || ctrl.flow_lr)

  require(ev.peLatency == 0 || ev.peLatency == 1)
  if (ev.peLatency == 0) {
    // Keep the legacy implementation selectable for baseline comparison.
    val exp2Done = Reg(Bool())
    when(fire) {
      when(ctrl.exp2) {
        exp2Done := exp2Done || macUnit.io.out_exp2
      }.otherwise {
        exp2Done := false.B
      }
      when(ctrl.load_reg_li) {
        reg := io.l_input.bits
      }.elsewhen(ctrl.load_reg_ui) {
        reg := ev.viewAasE(io.u_input.bits)
      }.elsewhen(ctrl.update_reg || (macUnit.io.out_exp2 && !exp2Done)) {
        reg := macUnit.io.out_elemType
      }
    }
    io.d_output.bits := Mux(ctrl.mac && ctrl.acc_ui, macUnit.io.out_accType, io.u_input.bits)
    io.d_output.valid := fire && (ctrl.mac && ctrl.acc_ui || ctrl.flow_ud)
    io.u_output.bits := Mux(ctrl.mac && !ctrl.acc_ui, macUnit.io.out_accType, io.d_input.bits)
    io.u_output.valid := fire && (ctrl.mac && !ctrl.acc_ui || ctrl.flow_du)
  } else {
    // Delay the writeback token with the after-add pipeline. A dependent
    // operation may first read reg two cycles after its producer is issued.
    val updateRegD = RegNext(fire && ctrl.update_reg, false.B)
    val expFire = fire && ctrl.exp2
    val expActive = RegInit(false.B)
    val expInput = Reg(elemType)
    val expIndex = RegInit(0.U(log2Ceil(ev.exp2PwlPieces).max(1).W))
    val expLast = expFire && expIndex === (ev.exp2PwlPieces - 1).U
    val expFirst = expFire && !expActive
    val expFireD = RegNext(expFire, false.B)
    val expLastD = RegNext(expLast, false.B)
    val expHit = expFireD && macUnit.io.out_exp2
    val expHitValue = Reg(elemType)
    val expHitValid = RegInit(false.B)
    val expAcceptedHit = expHit && !expHitValid
    val pendingWriteback = updateRegD || expLastD

    // These contracts keep the normalization/rounding result behind reg;
    // bypassing it into a dependent FMA would reconnect the timing path.
    when(fire && pendingWriteback) {
      assert(!(ctrl.load_reg_li || ctrl.load_reg_ui), "PE load collides with pending writeback")
      assert(!(ctrl.mac || ctrl.exp2 || ctrl.update_reg), "PE resident dependency issued before writeback")
    }
    when(expActive && fire && !ctrl.exp2) {
      assert(!(ctrl.load_reg_li || ctrl.load_reg_ui || ctrl.update_reg), "PE resident input modified during EXP2 scan")
    }
    when(expHit) {
      assert(!expHitValid, "PE EXP2 scan matched more than one segment")
    }
    when(expLastD) {
      assert(expHit || expHitValid, "PE EXP2 scan finished without a matching segment")
    }

    // Every segment must see the original input, even if an earlier segment
    // hits. Save the hit and commit only when the final segment has drained.
    macUnit.io.in_a := Mux(expActive && ctrl.exp2, expInput, reg)
    when(expFire) {
      when(expFirst) {
        expInput := reg
        expActive := true.B
        expHitValid := false.B
      }
      when(expLast) {
        expIndex := 0.U
        expActive := false.B
      }.otherwise {
        expIndex := expIndex + 1.U
      }
    }
    when(expAcceptedHit) {
      expHitValue := macUnit.io.out_elemType
      expHitValid := true.B
    }

    when(fire && ctrl.load_reg_li) {
      reg := io.l_input.bits
    }.elsewhen(fire && ctrl.load_reg_ui) {
      reg := ev.viewAasE(io.u_input.bits)
    }.elsewhen(expLastD && (expAcceptedHit || expHitValid)) {
      reg := Mux(expAcceptedHit, macUnit.io.out_elemType, expHitValue)
    }.elsewhen(updateRegD) {
      reg := macUnit.io.out_elemType
    }

    val downMacD = RegNext(ctrl.mac && ctrl.acc_ui)
    val upMacD = RegNext(ctrl.mac && !ctrl.acc_ui)
    val downBypassD = RegNext(io.u_input.bits)
    val upBypassD = RegNext(io.d_input.bits)
    io.d_output.bits := Mux(downMacD, macUnit.io.out_accType, downBypassD)
    io.d_output.valid := RegNext(fire && (ctrl.mac && ctrl.acc_ui || ctrl.flow_ud), false.B)
    io.u_output.bits := Mux(upMacD, macUnit.io.out_accType, upBypassD)
    io.u_output.valid := RegNext(fire && (ctrl.mac && !ctrl.acc_ui || ctrl.flow_du), false.B)
  }
}
