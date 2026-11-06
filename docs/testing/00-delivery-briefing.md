# 交付答辩材料索引

本目录内容已按"答辩时可直接打开"的方式整理。每个条目都标注了**用途**与**建议放置位置**。

## 一、报告正文

| 文档 | 回答什么问题 | 建议页 |
| --- | --- | --- |
| [黑盒测试报告](04-blackbox-test-report.md) | 从用户视角，产品真的能用吗？ | 3～5 页 |
| [白盒测试报告](05-whitebox-test-report.md) | 从代码视角，工程质量可靠吗？ | 2～3 页 |
| [测试总览](03-test-report.md) | 一页看懂测试规模、门禁与结论 | 1 页 |
| [测试用例](02-test-cases.md) | 每条需求都有对应用例吗？ | 附录 |
| [验收标准](../requirements/02-acceptance-criteria.md) | 需求与验收怎么对齐的？ | 附录 |

## 二、截图证据（`evidence/`）

全部为 1440x900 离屏渲染，中文字体已正确装载，可直接插入 PPT。

| 文件 | 内容 | 建议配文 |
| --- | --- | --- |
| `page-dashboard.png` | 工作台：值守概览、四个指标卡、重点区域画面、待处理险情 | "一屏掌握现场态势" |
| `page-live.png` | 实时画面：场所/区域/状态筛选、6 台摄像头、离线红标、待检测标 | "多摄像头多区域统一管理" |
| `page-incidents.png` | 险情中心：筛选、列表、详情处置区（含"补充位置"） | "险情从发现到闭环全程留痕" |
| `page-devices.png` | 区域与设备：场所管理、区域卡片、摄像头接入 | "设备接入所见即所得" |
| `page-assistant.png` | 现场问询：问询记录、问询范围、示例问题 | "用一句话问清现场情况" |
| `page-settings.png` | 系统设置：品牌横幅、通知、问询、国标、本地环境 | "无需IT能力也能配置" |

## 三、录屏证据（`evidence/`）

| 文件 | 时长 | 看什么 |
| --- | --- | --- |
| `live-view-real-footage.mp4` | 8.3 秒 / 50 帧 / 1120x720 | **平台实时画面播放客户自己的 L107 监控录像**。画面含原始 OSD 时间戳与真实场景，是接入与解码链路可用最直接的证明 |
| `console-walkthrough.mp4` | 逐页切换 | 控制台六个页面的完整巡览 |

## 四、答辩时最有力的三张牌

1. **客户自己的录像跑通了**（`live-view-real-footage.mp4`）
   —— 不是演示素材，是客户提供的真实监控录像，经平台实时解码播放。

2. **画面保真有据可查**（黑盒报告 BB-08）
   —— 交付帧与源文件做像素级梯度比对，任何丢包花屏都会让自动化用例立刻失败。
   这项用例在本轮直接促成了 RTP 接收缓冲区从默认值提升到 8 MB 的修复。

3. **工程门禁全绿**（白盒报告）
   —— 382 项用例全通过、分支覆盖率 77%、`ResourceWarning` 零泄漏、
   ruff / format / compileall 三道门禁全过。

## 五、现场可复现命令

~~~powershell
conda activate factoryguard

# 一键重跑黑盒用例（含真实录像接入）
python -m pytest tests/test_acceptance_blackbox.py tests/test_blackbox_real_media.py -v

# 重新生成截图与录屏
python -m tools.capture_evidence --db data/evidence.db --config config/evidence.json --out docs/testing/evidence
python -m tools.record_footage --out docs/testing/evidence --fps 6 --seconds 14

# 全量回归与门禁
python -W error::ResourceWarning -m pytest -q
ruff check src tests tools
ruff format --check src tests tools
~~~

## 六、说明

- 客户提供的三段原始监控录像保留在交付机器上，**不进入仓库**（已在 `.gitignore` 中排除）。
- `evidence/` 下的截图与录屏是交付物，**会随仓库一起提交**。
- 证据由 `tools/capture_evidence.py` 与 `tools/record_footage.py` 生成，可随时重建。
