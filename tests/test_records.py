import hashlib

from server.records import merkle_proof, merkle_root, verify_proof


def leaves(n):
    return [hashlib.sha256(f"record {i}".encode()).hexdigest() for i in range(n)]


def test_every_proof_verifies_for_trees_of_all_small_sizes():
    for n in range(1, 21):
        ls = leaves(n)
        root = merkle_root(ls)
        for i in range(n):
            assert verify_proof(ls[i], merkle_proof(ls, i), root), (n, i)


def test_tampered_leaf_or_proof_fails():
    ls = leaves(8)
    root = merkle_root(ls)
    proof = merkle_proof(ls, 3)
    assert not verify_proof(leaves(9)[8], proof, root)
    bad = [dict(p) for p in proof]
    bad[0]["hash"] = "00" * 32
    assert not verify_proof(ls[3], bad, root)


def test_single_leaf_root_is_the_leaf():
    ls = leaves(1)
    assert merkle_root(ls) == ls[0]
    assert merkle_proof(ls, 0) == []
