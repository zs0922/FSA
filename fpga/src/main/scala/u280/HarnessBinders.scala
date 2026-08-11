package chipyard.fpga.u280

import chisel3._

import chipyard.harness.{HarnessBinder}
import chipyard.iobinders._

class WithU280AXIMemHarnessBinder extends HarnessBinder({
  case (th: U280TestHarnessImpl, port: AXI4MemPort, chipId: Int) => {
    port.io <> DontCare
  }
})
