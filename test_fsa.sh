#!/bin/bash
# FSA U280 测试 - 不碰驱动，在 JTAG 编程后直接运行

echo "=== 1. 等待 HBM 校准 (15秒) ==="
sleep 15

echo "=== 2. 检查 PCIe ==="
lspci -k -s ab:00.0 2>/dev/null | grep -E "Control:|Region|Kernel"
if ! ls /dev/xdma0_user >/dev/null 2>&1; then
    echo "ERROR: /dev/xdma0_user 不存在，重新加载驱动"
    sudo rmmod xdma 2>/dev/null; sleep 1
    sudo modprobe xdma; sleep 2
fi

echo "=== 3. AXI-Lite 测试 ==="
echo -n "XDMA ID: "; sudo busybox devmem 0xe2000000 2>/dev/null
echo -n "FSA STATE: "; sudo busybox devmem 0xe0000008 2>/dev/null

echo "=== 4. DMA 测试 ==="
echo -n "DMA write: "
sudo head -c 512 /dev/urandom | sudo timeout 10 dd of=/dev/xdma0_h2c_0 bs=512 count=1 2>&1 | tail -1
echo -n "DMA read:  "
sudo timeout 10 dd if=/dev/xdma0_c2h_0 of=/tmp/test.bin bs=32 count=1 2>&1 | tail -1

echo "=== 5. FSAConfig ==="
grep mem_base /home/zhangsi/chipyard-fsa/fpga/generated-src/chipyard.fpga.u280.U280FPGATestHarness.EmptyU280Config/chipyard.fpga.u280.U280FPGATestHarness.EmptyU280Config.FSAConfig.json

echo "=== 6. FSA 测试 ==="
cd /home/zhangsi/chipyard-fsa/generators/fsa/python
sudo /home/zhangsi/.local/bin/uv run main.py --seq_q 16 --seq_kv 16 --config EmptyU280Config --engine FPGA
