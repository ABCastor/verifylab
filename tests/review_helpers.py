"""Bind fixture reviews to the trusted context the test deliberately reads."""

from verifylab.repo import Repo
from verifylab.status import meaning, meaning_digest


def meaning_args(item_id="add-zero"):
    repo = Repo.open()
    item = repo.trusted_item(item_id) or repo.load_item(item_id)
    basis = meaning(repo, item)
    return ["--expected-meaning-digest", meaning_digest(basis)] if basis is not None else []
