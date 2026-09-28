# 测试记录

测试日期：2026-09-28（v2 多用户）  
环境：Windows · Python 3.12.13 · 端口 8764  
数据根：`data/app.sqlite` + `data/users/<id>/timeline.db`

## 0. 多用户 / 隔离 / API Key（`tests/test_multiuser.py`）

结果：**全部通过**

| 检查项 | 结果 |
| --- | --- |
| 未登录被拒 401 | PASS |
| 管理员登录 | PASS |
| 创建成员 | PASS |
| 成员登录 | PASS |
| 分库隔离（成员看不到管理员数据） | PASS |
| 创建 API Key（明文只显示一次） | PASS |
| Bearer Key 写入 | PASS |
| 吊销后立即失效 | PASS |
| API Key + AI ingest | PASS |
| OpenAPI 含鉴权说明 | PASS |

## 1. 端到端用户流（`tests/test_flow.py`）

## 1. 端到端用户流（`tests/test_flow.py`）

结果：**18 / 18 通过**

| 步骤 | 结果 | 说明 |
| --- | --- | --- |
| 服务健康检查 | PASS | `GET /api/health` |
| 保存今日记录 | PASS | PUT 体重/睡眠/心情/运动 |
| 读回今日记录 | PASS | 体重 66.6 |
| 越界体重被拒绝 | PASS | 999 kg → 400，范围 20–300 |
| 部分字段保存 | PASS | 仅心情 |
| 缺失值保持 null | PASS | 图表序列 `None`，不是 0 |
| 问题样例 CSV 预览 | PASS | 13 行，识别非法日期/数字 |
| 重复/冲突识别 | PASS | 文件内重复 + 与库冲突 |
| 问题样例导入 skip | PASS | 写入/跳过/错误分行报告 |
| 打鼾 CSV 自动映射 | PASS | date/snore_burden/weight/sleep_quality |
| 导出 CSV | PASS | 含表头 |
| 导出 JSON 备份 | PASS | format=mimo-personal-timeline-backup |
| 备份恢复 merge | PASS | 12 条记录恢复 |
| 月报生成 | PASS | 含 stats |
| 月报 Markdown 导出 | PASS | 标题与统计表 |
| 相关性分析 | PASS | 含 n 与免责声明 |
| 创建自定义指标 | PASS | resting_hr_test |
| 写入自定义指标值 | PASS | 58 bpm |
| 重启/重复读取仍在 | PASS | 值仍为 58 |

## 2. 导入策略（`tests/test_import_strategies.py`）

| 策略 | 结果 | 验证点 |
| --- | --- | --- |
| overwrite | PASS | 体重 70→69.5；未出现在文件中的 mood=2 被保留 |
| merge_empty | PASS | 已有 weight/mood 不被覆盖 |
| fail | PASS | 存在重复时整次导入取消，`ok=false` |
| 一周样例 + merge_empty | PASS | 新日期写入，空值补齐 |
| 单位换算 lb→kg | PASS | 150 lb ≈ 68.039 kg |

## 3. 完整用户流对照

「记录一天 → 重启仍在 → 导入一周 CSV → 查看趋势 → 导出月报 → 备份恢复」

| 环节 | 验证方式 | 结果 |
| --- | --- | --- |
| 记录一天 | API + 浏览器日记录页 | PASS |
| 重启仍在 | 服务重启后 API 读回 | PASS |
| 导入一周 CSV | `week_sample.csv` | PASS |
| 查看趋势 | 浏览器趋势页（缺失显示「缺失」） | PASS |
| 导出月报 | Markdown + 打印样式 | PASS |
| 备份恢复 | JSON merge/replace API | PASS |

## 4. 浏览器 UI 冒烟

- 首启引导弹窗：显示流程说明与跨午夜约定
- 首页：今日快照、14 天趋势图、待补日期、月历速览
- 日记录：内置 + 自定义指标、范围提示、保存/删除
- 趋势：多指标叠加、日/周/月、缺失标签、相关分析、备注搜索
- 月报：统计表、图表（缺失标注）、可编辑总结、导出按钮

## 5. 负面与边界

| 场景 | 结果 |
| --- | --- |
| 非法日期 `2026-02-30` | 记为错误行，不写入 |
| 非法数字 `not-a-number` | 记为错误行，不写入 |
| 空值字段 | 保持缺失，不写 0 |
| 同日多行 CSV | 默认 skip 只保留第一行；不静默覆盖 |
| 跨月 8/31、9/1、9/30、10/1 | 正确分月归属 |
| 跨午夜睡眠 | 约定写在起床日（文档 + UI 提示） |

## 6. 复现命令

```powershell
.\start.ps1
# 另开终端
python tests\test_flow.py
python tests\test_import_strategies.py
```
