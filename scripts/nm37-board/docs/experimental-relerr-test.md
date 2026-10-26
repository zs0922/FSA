# 实验 bitstream 与 RelErr 板测

完整、可迁移的命令统一维护在[主 README](../README.md)，避免多个入口的路径和参数不一致。

- 从零重建：第1～5节，固定工具和硬件提交，安装依赖，编译、仿真、布局布线。
- 已有 routed DCP：第6节直接实验导出，不要求手写CDC审查JSON。
- 烧录与设备确认：第7节，选实际NM37 target/BDF。
- 查看原来的MAE/MSE/MaxErr/RelErr：第8节，使用对应版本源码和生成配置。
- 换服务器或正式交付：第9节。

`run_experimental_attention.py`调用原main.py的计算与比较逻辑，同时记录日志和文件身份；它不自动选择板卡，也不烧录。seq16正常后再运行seq1024；结果目录不能覆盖。

FSA与PyEasyFloat两行均对Torch比较，不是两者之间的逐位一致性检查。需要逐位检查和重复性能统计时使用主README中的统一board_test.py流程。
