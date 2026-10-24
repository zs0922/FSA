# FSA / NM37 FPGA 复现入口

本仓库的 NM37 交付包含 A70-P3（70 MHz）、A100（100 MHz）和 A110/APB55（DUT110 MHz / HBM APB55 MHz）。**完整命令及每一步的作用见 [NM37 复现 README](scripts/nm37-board/README.md)**，从 Git 获取源码开始，直到新 bit 上板和时间/bubble 对比。

| 顺序 | 做什么 | 得到什么 |
|---|---|---|
| 1 | 独立 clone 工具与三个硬件版本，checkout 文档中的精确 commit | 可追溯、互不混用的源码工作区 |
| 2 | 配置固定工具链，初始化锁定依赖；A70应用历史P3补丁，A100/A110恢复shell | 与各版本匹配的完整构建输入 |
| 3 | 编译生成器、定向/形式验证、Verilator系统仿真 | JAR、逐位比较结果及阶段通过记录；另有A70 seq1024命令 |
| 4 | elaborate、完整综合布局布线，人工审查本次STA/CDC | 本次RTL、DCP、时序/资源/CDC报告和验收证据 |
| 5 | 导出bit，绑定对应配置和统一板测软件 | 三份新bit及带校验和的板测包 |
| 6 | 在151确认JTAG/PCIe/XDMA，依次烧录、smoke及重复计时 | 正确性、运行时间、加速比和bubble对比表 |

决定从零重建时，直接按复现 README 第1–10节，在全新工作区执行，不需要旧工程或恢复步骤。若想继续原151工程，而该工程缺少 `assembly.passed.json`、留下日志/JAR，才执行 [A70安全恢复流程](scripts/nm37-board/docs/a70-build-recovery.md)，不要更新被冻结的驱动或补写成功标记。`sim 4/16` 指阵列尺寸，序列长度1024使用文档中的 `--seq 1024`。

只有一个主仓 `zs0922/FSA`，三个硬件版本对应不同分支/提交。复现分支已发布，151从源码完整复现并板测通过后再创建验收标签。下面保留本项目所基于的 Chipyard 框架说明。

---

![CHIPYARD](https://github.com/ucb-bar/chipyard/raw/main/docs/_static/images/chipyard-logo-full.png)

# Chipyard Framework [![Test](https://github.com/ucb-bar/chipyard/actions/workflows/chipyard-run-tests.yml/badge.svg)](https://github.com/ucb-bar/chipyard/actions)

## Quick Links

* **Latest Documentation**: https://chipyard.readthedocs.io/
* **User Question Forum**: https://groups.google.com/forum/#!forum/chipyard
* **Bugs and Feature Requests**: https://github.com/ucb-bar/chipyard/issues

## Using Chipyard

To get started using Chipyard, see the documentation on the Chipyard documentation site: https://chipyard.readthedocs.io/

## What is Chipyard

Chipyard is an open source framework for agile development of Chisel-based systems-on-chip.
It will allow you to leverage the Chisel HDL, Rocket Chip SoC generator, and other [Berkeley][berkeley] projects to produce a [RISC-V][riscv] SoC with everything from MMIO-mapped peripherals to custom accelerators.
Chipyard contains processor cores ([Rocket][rocket-chip], [BOOM][boom], [CVA6 (Ariane)][cva6]), vector units ([Saturn](saturn), [Ara](ara)), accelerators ([Gemmini][gemmini], [NVDLA][nvdla]), memory systems, and additional peripherals and tooling to help create a full featured SoC.
Chipyard supports multiple concurrent flows of agile hardware development, including software RTL simulation, FPGA-accelerated simulation ([FireSim][firesim]), automated VLSI flows ([Hammer][hammer]), and software workload generation for bare-metal and Linux-based systems ([FireMarshal][firemarshal]).
Chipyard is actively developed in the [Berkeley Architecture Research Group][ucb-bar] in the [Electrical Engineering and Computer Sciences Department][eecs] at the [University of California, Berkeley][berkeley].

## Resources

* Chipyard Documentation: https://chipyard.readthedocs.io/
* Chipyard (x FireSim) Tutorial: https://fires.im/tutorial-recent/
* Chipyard Basics slides: https://fires.im/asplos23-slides-pdf/02_chipyard_basics.pdf

## Need help?

* Join the Chipyard Mailing List: https://groups.google.com/forum/#!forum/chipyard
* If you find a bug or would like propose a feature, post an issue on this repo: https://github.com/ucb-bar/chipyard/issues

## Contributing

* See [CONTRIBUTING.md](/CONTRIBUTING.md)

## Attribution and Chipyard-related Publications

If used for research, please cite Chipyard by the following publication:

```
@article{chipyard,
  author={Amid, Alon and Biancolin, David and Gonzalez, Abraham and Grubb, Daniel and Karandikar, Sagar and Liew, Harrison and Magyar,   Albert and Mao, Howard and Ou, Albert and Pemberton, Nathan and Rigge, Paul and Schmidt, Colin and Wright, John and Zhao, Jerry and Shao, Yakun Sophia and Asanovi\'{c}, Krste and Nikoli\'{c}, Borivoje},
  journal={IEEE Micro},
  title={Chipyard: Integrated Design, Simulation, and Implementation Framework for Custom SoCs},
  year={2020},
  volume={40},
  number={4},
  pages={10-21},
  doi={10.1109/MM.2020.2996616},
  ISSN={1937-4143},
}
```

* **Chipyard**
    * A. Amid, et al. *IEEE Micro'20* [PDF](https://ieeexplore.ieee.org/document/9099108).
    * A. Amid, et al. *DAC'20* [PDF](https://ieeexplore.ieee.org/document/9218756).
    * A. Amid, et al. *ISCAS'21* [PDF](https://ieeexplore.ieee.org/abstract/document/9401515).

These additional publications cover many of the internal components used in Chipyard. However, for the most up-to-date details, users should refer to the Chipyard docs.

* **Generators**
    * **Rocket Chip**: K. Asanovic, et al., *UCB EECS TR*. [PDF](http://www2.eecs.berkeley.edu/Pubs/TechRpts/2016/EECS-2016-17.pdf).
    * **BOOM**: C. Celio, et al., *Hot Chips 30*. [PDF](https://old.hotchips.org/hc30/1conf/1.03_Berkeley_BROOM_HC30.Berkeley.Celio.v02.pdf).
      * **SonicBOOM (BOOMv3)**: J. Zhao, et al., *CARRV'20*. [PDF](https://carrv.github.io/2020/papers/CARRV2020_paper_15_Zhao.pdf).
      * **COBRA (BOOM Branch Prediction)**: J. Zhao, et al., *ISPASS'21*. [PDF](https://ieeexplore.ieee.org/document/9408173).
    * **Gemmini**: H. Genc, et al., *DAC'21*. [PDF](https://arxiv.org/pdf/1911.09925).
* **Sims**
    * **FireSim**: S. Karandikar, et al., *ISCA'18*. [PDF](https://sagark.org/assets/pubs/firesim-isca2018.pdf).
        * **FireSim Micro Top Picks**: S. Karandikar, et al., *IEEE Micro, Top Picks 2018*. [PDF](https://sagark.org/assets/pubs/firesim-micro-top-picks2018.pdf).
        * **FASED**: D. Biancolin, et al., *FPGA'19*. [PDF](https://people.eecs.berkeley.edu/~biancolin/papers/fased-fpga19.pdf).
        * **Golden Gate**: A. Magyar, et al., *ICCAD'19*. [PDF](https://davidbiancolin.github.io/papers/goldengate-iccad19.pdf).
        * **FirePerf**: S. Karandikar, et al., *ASPLOS'20*. [PDF](https://sagark.org/assets/pubs/fireperf-asplos2020.pdf).
        * **FireSim ISCA@50 Retrospective**: S. Karandikar, et al., *ISCA@50 Retrospective: 1996-2020*. [PDF](https://sites.coecis.cornell.edu/isca50retrospective/files/2023/06/Karandikar_2018_FireSim.pdf)
* **Tools**
    * **Chisel**: J. Bachrach, et al., *DAC'12*. [PDF](https://people.eecs.berkeley.edu/~krste/papers/chisel-dac2012.pdf).
    * **FIRRTL**: A. Izraelevitz, et al., *ICCAD'17*. [PDF](https://ieeexplore.ieee.org/document/8203780).
    * **Chisel DSP**: A. Wang, et al., *DAC'18*. [PDF](https://ieeexplore.ieee.org/document/8465790).
    * **FireMarshal**: N. Pemberton, et al., *ISPASS'21*. [PDF](https://ieeexplore.ieee.org/document/9408192).
* **VLSI**
    * **Hammer**: E. Wang, et al., *ISQED'20*. [PDF](https://www.isqed.org/English/Archives/2020/Technical_Sessions/113.html).
    * **Hammer**: H. Liew, et al., *DAC'22*. [PDF](https://dl.acm.org/doi/abs/10.1145/3489517.3530672).

## Acknowledgements

This work is supported by the NSF CCRI ENS Chipyard Award #2016662.

[hammer]:https://github.com/ucb-bar/hammer
[firesim]:https://fires.im
[ucb-bar]: http://bar.eecs.berkeley.edu
[eecs]: https://eecs.berkeley.edu
[berkeley]: https://berkeley.edu
[riscv]: https://riscv.org/
[rocket-chip]: https://github.com/freechipsproject/rocket-chip
[boom]: https://github.com/riscv-boom/riscv-boom
[firemarshal]: https://github.com/firesim/FireMarshal/
[cva6]: https://github.com/openhwgroup/cva6/
[gemmini]: https://github.com/ucb-bar/gemmini
[nvdla]: http://nvdla.org/
[saturn]: https://github.com/ucb-bar/saturn-vectors
[ara]: https://github.com/pulp-platform/ara


### Locally maintained FSA and easyfloat

`generators/fsa` and `generators/easyfloat` are ordinary source directories
tracked by this repository, not Git submodules. Edit, review, and commit their
files from the repository root. SBT and Python dependency paths are unchanged.
Other dependencies, including `fpga/fpga-shells`, remain submodules.

`generators/LOCAL_SOURCES.json` records the imported upstream commits and the
SHA-256 of all 89 imported files, including local modifications and new tests.
These hashes identify the migration snapshot, not future editable source state.
Existing copyright notices are retained; neither imported tree supplied a
standalone LICENSE file, and this migration does not establish a new license.

The U280/NM37/M1 patch scripts skip historical FSA/easyfloat patches for these
locally tracked trees, while still processing the remaining shell submodule.
Do not run `git submodule add` or replay the old core patches on these paths.

This is a source snapshot import, not a rewrite of upstream commit history.
Local upstream histories and working-tree backups are kept in the ignored
`vendor-migration-backup/` directory; upstream commit IDs remain in the manifest.
The existing A100 measurement archives remain historical evidence and are not
rewritten by this repository-ownership migration.

### NM37 A100 baseline

See [the reproducible NM37 A100 baseline](optimization-reports/NM37_A100_BASELINE.md)
for exact configuration, shell restoration, verification, implementation,
bitstream export, board tests, and archived evidence.

See [the NM37 A110 / APB55 experiment](optimization-reports/NM37_A110_APB.md)
for the independent HBM management clock, reset and CDC review, routed timing,
exported bitstream, and comparison with A100 and the original A110 experiment.
The A100 baseline remains available; A110 hardware operation still requires
board validation.
