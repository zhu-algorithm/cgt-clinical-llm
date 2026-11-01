"""fmed_gate —— 外部智能体贡献通道。

同意链的根：trusted_keys.json 里每一个公钥，都是仓库所有者合并一次 PR 才进去的。
贡献方无法自助登记，因此无法自助获得受理资格。
"""
from .envelope import build, verify, new_identity, seal, unseal, SignatureInvalid
from .schema import validate, ContributionRejected
from .tiers import admit, classify, TIERS, TierRefused
from .invariants import run as run_invariants, assert_ok, InvariantViolation, PROTECTED
from .review import review
