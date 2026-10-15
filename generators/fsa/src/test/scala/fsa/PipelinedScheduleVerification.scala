package fsa

import chisel3._
import fsa.arithmetic.HasArithmeticParams
import java.nio.file.{Files, Paths}

/** Checks transported score identities and resident dependencies, independently
  * of the floating-point implementation; also emits every row/column event.
  */
object PipelinedScheduleVerification {
  val ap = new HasArithmeticParams {
    val reciprocalLatency = 2
    val exp2PwlPieces = 8
    val accLatency = 3
    override val peLatency = 1
    override val cmpLatency = 1
    override val spadLatency = 2
  }

  def check(rows: Int, cols: Int): PipelinedScoreTiming = {
    val p = new AttentionScoreExecPlan(rows, cols, ap)
    val t = PipelinedScoreTiming(rows, cols, ap)
    val forward = p.flow_ud.raw_execution_plan
    val loads = p.load_reg_ui.raw_execution_plan
    val subtract = p.update_reg.raw_execution_plan
    // Track each CMP score token through the real per-row forwarding plan.
    for (column <- 0 until cols; score <- 0 until rows) {
      var arrival = t.scoreUpdate + score + t.dc + column
      for (row <- 0 until rows - score) {
        val localCycle = arrival - column
        if (row == rows - score - 1) {
          assert(loads(row)(localCycle), s"score $score missing load row=$row cycle=$arrival")
          assert(localCycle == t.scoreLoad(row))
        } else {
          assert(forward(row)(localCycle), s"score $score dropped row=$row cycle=$arrival")
          arrival += t.d
        }
      }
    }
    for (row <- 0 until rows) {
      val sub = t.sub + row * t.d
      val scale = t.scale + row * t.d
      val exp = t.exp + row * t.d
      val sum = t.sum + row * t.d
      assert(sub >= t.scoreLoad(row) + 1)
      assert(scale >= sub + t.commitGap)
      assert(exp >= scale + t.commitGap)
      assert(sum >= exp + ap.exp2PwlPieces - 1 + t.commitGap)
      val zero = t.zeroFlow + (rows - row - 1) * t.d
      val lastScoreMac = t.ls + (rows - row - 1) * t.d + rows - 1
      assert(zero > lastScoreMac)
      assert(zero < scale, s"scale reads an uncleared d_input row=$row")
      assert(subtract(row)(sub) && subtract(row)(scale))
      // A pending resident writeback must not encounter another resident token.
      for (cycle <- Seq(sub, scale, exp + ap.exp2PwlPieces - 1)) {
        val pending = cycle + ap.peLatency
        assert(!p.mac.raw_execution_plan(row).lift(pending).getOrElse(false))
        assert(!loads(row).lift(pending).getOrElse(false))
        assert(!subtract(row).lift(pending).getOrElse(false))
        assert(!p.exp2.raw_execution_plan(row).lift(pending).getOrElse(false))
      }
    }
    assert(p.acc_ctrl.head.cycle == t.diffAtAccumulator)
    assert(p.acc_read.head.cycle + 1 == t.sumAtAccumulator)
    assert(p.accumulateMaxCycle == t.sumAtAccumulator + 1 + ap.accLatency)
    assert(p.conflictFreeCycle.contains(if (t.conservative) p.accumulateMaxCycle - 1 else t.sum - 1))
    if (!t.conservative) {
      // EHR exposes conflictFree in the marker cycle itself: the following
      // instruction's timer zero is marker+1, with no assumed extra bubble.
      val valueBase = p.conflictFreeCycle.get + 1
      val value = new AttentionValueExecPlan(rows, cols, ap)
      assert(valueBase + value.acc_ctrl.head.cycle > t.sumAtAccumulator)
      assert(valueBase + value.acc_read.head.cycle > t.sumAtAccumulator - 1)
      assert(t.diffAtAccumulator + 1 + ap.accLatency + ap.accLatency + 1 <= t.sumAtAccumulator)
      val loadBase = value.conflictFreeCycle.get + 1
      for (row <- 0 until rows) {
        assert(loadBase + t.ls > t.ls + row * t.d + rows - 1)
      }
    }
    t
  }

  def main(args: Array[String]): Unit = {
    val dimensions = Seq((4, 4), (4, 8), (8, 4), (8, 8), (16, 16))
    dimensions.foreach { case (r, c) => check(r, c) }
    val t = check(16, 16)
    val events = for (r <- 0 until 16; c <- 0 until 16) yield {
      s"""{"row":$r,"column":$c,"load":${t.scoreLoad(r) + c},"subtract":${t.sub + r * t.d + c},"scale":${t.scale + r * t.d + c},"exp_first":${t.exp + r * t.d + c},"exp_last":${t.exp + r * t.d + c + 7},"sum":${t.sum + r * t.d + c}}"""
    }
    val json = s"""{"cycle_basis":"execution plan, before common controller Pipe","rows":16,"cols":16,"score_cmp_first":${t.scoreUpdate},"cmp_hop":${t.dc},"pe_vertical_hop":${t.d},"spad_entry":${t.ls},"resident_commit_gap":${t.commitGap},"acc_exp_s1":${t.diffAtAccumulator},"acc_exp_s2":${t.diffAtAccumulator + 1 + ap.accLatency},"acc_sum_read":${t.sumAtAccumulator - 1},"acc_sum_command":${t.sumAtAccumulator},"done":${t.sumAtAccumulator + ap.accLatency},"events":[${events.mkString(",\n")}]}"""
    args.headOption.foreach(path => Files.writeString(Paths.get(path), json + "\n"))
    println(s"PASS: transported-score and resident-dependency schedules ${dimensions.mkString(", ")}; R16 score load ${t.scoreLoad(0)}..${t.scoreLoad(15)}, score done ${t.sumAtAccumulator + ap.accLatency}.")
  }
}
