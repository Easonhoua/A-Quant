"""策略评审工具包。

用途：在真实行情缓存上回答"这套规则到底有没有边际"，而不是靠感觉调参。

模块：
    panel      把引擎的 WildER 递推一次算完整条序列（引擎是逐日重算，O(n^2)），
               并提供与 backend.engine.signals 逐日对照的一致性检查。
    quality    快照体检：as_of 覆盖度、除权跳空、停牌缺口，以及"日报现在会选出什么"。
    study      信号事件研究、S1 参数敏感性、真实引擎抽样回测。

命令见 `python -m tools.review --help`。所有输出写到 data/review/。
"""
