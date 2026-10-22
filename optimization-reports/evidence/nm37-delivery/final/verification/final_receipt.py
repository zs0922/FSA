import hashlib,json,shutil,subprocess
from pathlib import Path
b=Path('/home/zhangsi/tmp/FSA-m1/nm37-a70-rebuild-20261022');d=b/'delivery-final'; repo=b/'src'; commit='d589b230b975730c108426591e552c056008e82a'
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for c in iter(lambda:f.read(1048576),b''):h.update(c)
 return h.hexdigest()
board=json.loads((b/'review/final-H1b-board-validation.json').read_text());source=json.loads((b/'review/final-H1b-source-validation.json').read_text())
assert board['passed'] and source['passed'] and board['integration_commit']==source['integration_commit']==commit
proofs=['final-H1b-board-validation.json','final-H1b-source-validation.json','final-H1b-delivery-spec-review.json','final-H1b-dependency-copy.json','package-pax-fix-review.json','nm37-board-pax-full-tests.log','package-pax-after-fix.log','source-bundle-full-restore-acceptance.json','source-bundle-profile-compatibility.json','source-bundle-restored-vs-final-p3-v2.json','common-software-four-config-comparison.json','common-software-memory-topology.json','old-H1-writer-final-status.json']
v=d/'verification';v.mkdir()
for name in proofs:shutil.copyfile(b/'review'/name,v/name)
for identity in ['A70-P3','A100','A110-APB55']:shutil.copyfile(b/'review'/('final-H1b-'+identity+'-dryrun-v2')/'run.json',v/(identity+'-dry-run.json'))
for name in ['verify_final_board.py','verify_final_source.py','final_receipt.py']:shutil.copyfile(b/'delivery-plan-final'/name,v/name)
archives=[]
for filename in ['nm37-board-transfer.tar.gz','nm37-source-transfer.tar.gz']:
 p=d/filename; digest=sha(p);size=p.stat().st_size
 expected=board['archive'] if filename.startswith('nm37-board') else {'sha256':source['archive_sha256'],'bytes':source['archive_bytes']}
 assert digest==expected['sha256'] and size==expected['bytes'];archives.append({'path':filename,'sha256':digest,'bytes':size})
(d/'SHA256SUMS.archives').write_text(''.join(f"{x['sha256']}  {x['path']}\n" for x in archives))
identities=json.loads((b/'delivery-plan-final/delivery-source-identity.json').read_text())['hardware_identities']
for ident in identities: ident['manifest_path']='nm37-board-transfer/'+ident['configuration_id']+'.manifest.json'
receipt={'schema':1,'kind':'nm37-final-delivery','status':'verified_ready_for_manual_transfer_and_board_test','formal_delivery_directory':'delivery-final','superseded_directory':'delivery','integration_commit':commit,'integration_commit_role':'tools/software/reports; each hardware identity listed separately','archives':archives,'board_package_sha256':board['package_sha256'],'hardware_configurations':identities,'excluded_board_configurations':{'A70-P2':'routed DUT setup slack -0.182 ns; no accepted bitstream; history remains in source bundle'},'source_profiles':source['profiles'],'main_bundle_sha256':source['main_bundle_sha256'],'verification':{'unit_tests_passed':59,'package_tests_passed':14,'actual_board_archive_extract_and_inventory_verify':True,'three_configuration_dry_runs_passed':True,'offline_device_parameters_are_fixtures':True,'archive_long_basename_and_normalized_pax_checked':True,'relative_report_and_tool_links_checked':board['relative_report_and_tool_links_verified'],'source_archive_members_stream_hashed':source['all_archive_members_stream_hashed'],'all_169_standalone_bundles_empty_database_verified':True,'historical_full_restore_dependency_paths':175,'a100_a110_matching_dependency_paths':173,'unique_dependency_bundles':168,'explicit_recursive_exclusions':11,'root_out_of_scope_gitlinks':20},'boundaries':{'hardware_programmed':False,'hardware_tested':False,'ssh_or_remote_operations_performed':False,'push_performed':False,'toolchains_vivado_drivers_python_wheels_included':False,'a100_a110_pnr_rerun_in_this_packaging_task':False,'a100_source_is_reconstructed_snapshot_not_original_clean_commit_build':True,'h2_delivery_receipt_commit_not_inside_archives':True},'verification_files':{p.relative_to(d).as_posix():{'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(v.iterdir())}}
(d/'delivery-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
rows='\n'.join(f"| `{x['path']}` | {x['bytes']:,} | {x['bytes']/1e9:.3f} GB | `{x['sha256']}` |" for x in archives)
text='''# NM37 正式交付（H1b）

只有本目录 `delivery-final` 是正式交付。旧 `delivery` 是已完成但被替代的 H1 尝试，不能混用其归档或校验单。

集成源码提交：`COMMIT`。归档的工具、软件和报告取自该提交；每个 bit 的硬件来源独立记录在 manifest 中。后续 H2 仅提交交付回执，不回写进本次归档，避免自引用。

| 归档 | 字节 | 十进制大小 | SHA-256 |
|---|---:|---:|---|
ROWS

板测包带完整原始/归档证据，因此体积约 1.4 GB；源码包包括 9 个主仓版本与 175 项递归依赖（168 个独立 bundle）。A100/A110 使用其中相同固定版本的 173 项依赖。Vivado、许可、编译器安装、驱动、Python wheels 不包含在内。

板测包的三份配置为 A70-P3、A100、A110-APB55。A70-P3 是修复历史构建顺序后重新实现的 P3；P2 未通过时序，不作为可用 bit 交付。A100 的 `0f1a643a` 是经逐文件核对的重建源码快照，原 bit 由较早工作树生成，不能称为该提交的干净构建。

## 到 10.128.157.151 测试

将下面的 `USER` 替换为实际用户名；命令是待执行操作，当前没有访问或测试目标服务器。先确认目标目录存在。

```bash
scp nm37-board-transfer.tar.gz nm37-source-transfer.tar.gz SHA256SUMS.archives \\
  USER@10.128.157.151:/work/nm37-transfer/
ssh USER@10.128.157.151
cd /work/nm37-transfer
sha256sum -c SHA256SUMS.archives
mkdir /work/nm37-board
tar -xzf nm37-board-transfer.tar.gz -C /work/nm37-board
cd /work/nm37-board
python3 board-tools/package.py verify --root .
```

先通过可信渠道核对校验单；完整板测步骤见解包后的 [151 操作单](nm37-board-transfer/board-tools/docs/151-board-test.md)。不需要 clone 仓库。手动加载 `bitstreams/A70-P3.bit`、`bitstreams/A100.bit` 或 `bitstreams/A110-APB55.bit`，每换 bit 重新确认设备/BDF，并只运行对应 manifest。禁止未换 bit 就轮跑三个配置。结果写在包外新目录，每次实际测试使用外部 timeout；脚本不会自动烧录或 push。

## 离线恢复源码

```bash
cd /work/nm37-transfer
mkdir /work/nm37-source
tar -xzf nm37-source-transfer.tar.gz -C /work/nm37-source
cd /work/nm37-source
sha256sum -c SOURCE_SHA256SUMS
```

逐版本恢复、安装环境、编译/验证/布局布线命令见 [源码重建说明](nm37-source-transfer/docs/source-reproduction.md)。Git bundle 包括固定依赖对象，不依赖原工作区 alternates 或远程仓库；工具链仍需目标机预备。历史 P2/P3 原始 profile 加 installer 可重建；A100/A110 的复现入口与原始硬件提交分别记录。

## 本地交付验收范围

59 项测试通过；正式板测归档已实际安全解包、逐文件核验，并对三个 manifest 完成无硬件 dry-run；长文件名、规范化 PAX 元数据、相对文档链接均已检查。源码归档全部成员通过流式 SHA 校验，最终主 bundle 和 168 个依赖 bundle 均在空对象库验证。此前已实际恢复并验证全部 175 项历史依赖；本次没有重复全量源码恢复，也未重新对 A100/A110 运行布局布线。

机器可读详情见 [delivery-receipt.json](delivery-receipt.json) 和 [verification](verification/)。当前尚未烧录/板测；在151上测试通过后再整理性能与 bubble 结果，并决定 push。
'''.replace('COMMIT',commit).replace('ROWS',rows)
(d/'README.md').write_text(text)
print(json.dumps({'receipt':str(d/'delivery-receipt.json'),'sha256':sha(d/'delivery-receipt.json'),'archives':archives},indent=2))
