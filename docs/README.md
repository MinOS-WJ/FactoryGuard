# FactoryGuard 智守文档中心

本目录是当前产品交付文档的唯一入口。根目录 `README.md` 只保留快速开始和产品概览，详细内容按以下结构维护。

## 需求

- [需求分析](requirements/01-requirements-analysis.md)
- [验收标准](requirements/02-acceptance-criteria.md)

## 设计

- [系统架构](design/01-architecture.md)
- [数据与配置](design/02-data-and-config.md)
- [界面与交互设计](design/03-ui-ux.md)
- [商户控制台产品说明](product/merchant-console.md)

## 测试

- [交付答辩材料索引](testing/00-delivery-briefing.md)
- [测试计划](testing/01-test-plan.md)
- [测试用例](testing/02-test-cases.md)
- [测试报告](testing/03-test-report.md)
- [黑盒测试报告](testing/04-blackbox-test-report.md)
- [白盒测试报告](testing/05-whitebox-test-report.md)
- [交付证据（截图与录屏）](testing/evidence/)

## 运维与使用

- [部署手册](operations/01-deployment.md)
- [用户手册](operations/02-user-guide.md)
- [维护手册](operations/03-maintenance.md)

## 文档维护规则

1. 新功能先更新需求与验收标准，再更新设计文档；
2. 测试用例必须关联验收标准或明确的工程风险；
3. 测试报告记录实际命令、环境、结果和未验证范围；
4. 实际部署参数、密钥和现场设备信息不得提交到仓库；
5. 产品行为变化时同步更新根 README、用户手册和维护手册。
