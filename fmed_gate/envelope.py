"""贡献信封：规范化 JSON + Ed25519 签名 + 可选定向加密。

设计要点
--------
* **签名针对规范化字节**，不是针对 dict。否则键序或空白一变，签名就对不上。
* **CI 只验证信封**，不解密内容——解密私钥只在仓库所有者手里。
* 加密用 X25519 sealed box（匿名定向加密）：任何人可加密给所有者，
  只有所有者能解密；但**这不是把患者数据搬进公开仓库的许可**，
  见 schema.py 的 PHI 拒绝。密文一旦提交即永久存在，密钥会泄露。
"""
from __future__ import annotations
import json, hashlib, base64, datetime

from nacl.signing import SigningKey, VerifyKey
from nacl.public import SealedBox, PrivateKey, PublicKey
from nacl.exceptions import BadSignatureError
from nacl.encoding import RawEncoder

SCHEMA_VERSION = "fmed-gate/1"


def canonical(obj) -> bytes:
    """规范化序列化——签名与摘要的唯一依据。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest(obj) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def unb64(s: str) -> bytes:
    return base64.b64decode(s)


def new_identity():
    """生成一对智能体身份密钥。公钥登记入 trusted_keys.json 需经所有者合并。"""
    sk = SigningKey.generate()
    return dict(signing_key=b64(bytes(sk)),
                public_key=b64(bytes(sk.verify_key)))


def seal(plaintext: bytes, owner_pubkey_b64: str) -> str:
    """定向加密给仓库所有者。"""
    box = SealedBox(PublicKey(unb64(owner_pubkey_b64)))
    return b64(box.encrypt(plaintext))


def unseal(ciphertext_b64: str, owner_privkey_b64: str) -> bytes:
    """仅所有者可执行；CI 永远不持有此密钥。"""
    box = SealedBox(PrivateKey(unb64(owner_privkey_b64)))
    return box.decrypt(unb64(ciphertext_b64))


def build(payload: dict, agent_id: str, signing_key_b64: str,
          tier: str, intent: str) -> dict:
    """构造一个可提交的贡献信封。"""
    body = dict(schema=SCHEMA_VERSION, agent_id=agent_id, tier=tier,
                intent=intent,
                submitted_at=datetime.datetime.now(datetime.UTC)
                             .isoformat(timespec="seconds"),
                payload=payload)
    sk = SigningKey(unb64(signing_key_b64))
    sig = sk.sign(canonical(body), encoder=RawEncoder).signature
    return dict(body=body, signature=b64(sig), digest=digest(body))


class SignatureInvalid(Exception):
    """签名无效或密钥未登记——拒绝受理，不降级为告警。"""


def verify(env: dict, trusted_keys: dict) -> dict:
    """验签。trusted_keys: {agent_id: public_key_b64}。

    未登记的 agent_id 一律拒绝——**登记新公钥本身必须经所有者合并 PR**，
    这是整个同意链的根。
    """
    body = env.get("body") or {}
    aid = body.get("agent_id")
    if body.get("schema") != SCHEMA_VERSION:
        raise SignatureInvalid(f"信封版本不符：{body.get('schema')}")
    if aid not in trusted_keys:
        raise SignatureInvalid(
            f"智能体「{aid}」的公钥未登记。登记需由仓库所有者合并一次 PR——"
            f"这是同意链的根，不可由贡献方自助完成。")
    try:
        VerifyKey(unb64(trusted_keys[aid])).verify(
            canonical(body), unb64(env["signature"]))
    except (BadSignatureError, KeyError, TypeError) as e:
        raise SignatureInvalid(f"签名验证失败：{type(e).__name__}") from e
    if env.get("digest") != digest(body):
        raise SignatureInvalid("摘要与内容不符——信封被改动过")
    return body
