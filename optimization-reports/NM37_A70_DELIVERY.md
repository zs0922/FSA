# NM37 A70/A100/A110 最终交付验收

正式交付目录为 `/home/zhangsi/tmp/FSA-m1/nm37-a70-rebuild-20261022/delivery-final`。旧 `delivery` 是已完成但被替代的 H1 尝试，不能混用其归档或校验单。

集成源码提交：`d589b230b975730c108426591e552c056008e82a`。归档的工具、软件和报告取自该提交；每个 bit 的硬件来源独立记录在 manifest 中。后续 H2 仅提交交付回执，不回写进本次归档，避免自引用。

| 归档 | 字节 | 十进制大小 | SHA-256 |
|---|---:|---:|---|
| `nm37-board-transfer.tar.gz` | 1,434,503,752 | 1.435 GB | `d6916f7a272e7b1fb73410776ab78d50f10a720a1ba071f4fda68333aaa98330` |
| `nm37-source-transfer.tar.gz` | 6,176,289,879 | 6.176 GB | `e6d651e42f5608a2afb827cd7981387bfbeda186582c89d3d9acc341f81db825` |

板测包带完整原始/归档证据，因此体积约 1.4 GB；源码包包括 9 个主仓版本与 175 项递归依赖（168 个独立 bundle）。A100/A110 使用其中相同固定版本的 173 项依赖。Vivado、许可、编译器安装、驱动、Python wheels 不包含在内。

板测包的三份配置为 A70-P3、A100、A110-APB55。A70-P3 是修复历史构建顺序后重新实现的 P3；P2 未通过时序，不作为可用 bit 交付。A100 的 `0f1a643a` 是经逐文件核对的重建源码快照，原 bit 由较早工作树生成，不能称为该提交的干净构建。

## 到 10.128.157.151 测试

将下面的 `USER` 替换为实际用户名；命令是待执行操作，当前没有访问或测试目标服务器。先确认目标目录存在。

```bash
scp nm37-board-transfer.tar.gz nm37-source-transfer.tar.gz SHA256SUMS.archives \
  USER@10.128.157.151:/work/nm37-transfer/
ssh USER@10.128.157.151
cd /work/nm37-transfer
sha256sum -c SHA256SUMS.archives
mkdir /work/nm37-board
tar -xzf nm37-board-transfer.tar.gz -C /work/nm37-board
cd /work/nm37-board
python3 board-tools/package.py verify --root .
```

先通过可信渠道核对校验单；完整板测步骤见解包后的 [151 操作单](../scripts/nm37-board/docs/151-board-test.md)。不需要 clone 仓库。手动加载 `bitstreams/A70-P3.bit`、`bitstreams/A100.bit` 或 `bitstreams/A110-APB55.bit`，每换 bit 重新确认设备/BDF，并只运行对应 manifest。禁止未换 bit 就轮跑三个配置。结果写在包外新目录，每次实际测试使用外部 timeout；脚本不会自动烧录或 push。

## 离线恢复源码

```bash
cd /work/nm37-transfer
mkdir /work/nm37-source
tar -xzf nm37-source-transfer.tar.gz -C /work/nm37-source
cd /work/nm37-source
sha256sum -c SOURCE_SHA256SUMS
```

逐版本恢复、安装环境、编译/验证/布局布线命令见 [源码重建说明](../scripts/nm37-board/docs/source-reproduction.md)。Git bundle 包括固定依赖对象，不依赖原工作区 alternates 或远程仓库；工具链仍需目标机预备。历史 P2/P3 原始 profile 加 installer 可重建；A100/A110 的复现入口与原始硬件提交分别记录。

## 本地交付验收范围

59 项测试通过；正式板测归档已实际安全解包、逐文件核验，并对三个 manifest 完成无硬件 dry-run；长文件名、规范化 PAX 元数据、相对文档链接均已检查。源码归档全部成员通过流式 SHA 校验，最终主 bundle 和 168 个依赖 bundle 均在空对象库验证。此前已实际恢复并验证全部 175 项历史依赖；本次没有重复全量源码恢复，也未重新对 A100/A110 运行布局布线。

机器可读详情见 [delivery-receipt.json](evidence/nm37-delivery/final/delivery-receipt.json) 和 [verification](evidence/nm37-delivery/final/verification/)。当前尚未烧录/板测；在151上测试通过后再整理性能与 bubble 结果，并决定 push。

父级独立验收重新计算了两个完整归档的 SHA-256，核对 19 份验证文件、3 个硬件 manifest 和 9 个源码 profile，全部通过。见 [验收记录](evidence/nm37-delivery/final/parent-final-delivery-acceptance.json)。本提交仅补交交付文档和记录，归档源码基线仍为 `d589b230`。
