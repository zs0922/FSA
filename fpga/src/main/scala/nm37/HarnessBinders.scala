package chipyard.fpga.nm37

import chisel3._

import chipyard.harness.{HarnessBinder}
import chipyard.iobinders._

class WithNM37AXIMemHarnessBinder extends HarnessBinder({
  case (th: NM37TestHarnessImpl, port: AXI4MemPort, chipId: Int) => {
    port.io <> DontCare
  }
})
