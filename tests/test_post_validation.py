import asyncio
from pandas import Timestamp
import pytest

from main_logic import post as post_module
from main_logic.post import Post


class _AlwaysUsableAccount:
    def __init__(self, phone_number: str = "+1000000000"):
        self.phone_number = phone_number

    def is_usable(self) -> bool:
        return True


class _DummyClient:
    def __init__(self, phone_number: str = "+1000000000"):
        self.phone_number = phone_number
        self.account = _AlwaysUsableAccount(phone_number)


class _FakeDB:
    def __init__(self, stored_post: Post):
        self._stored_post = stored_post

    async def get_post(self, post_id):
        stored = self._stored_post
        return Post(
            post_id=stored.post_id,
            message_link=stored.message_link,
            chat_id=stored.chat_id,
            message_id=stored.message_id,
            message_content=stored.message_content,
            content_fetched_at=stored.content_fetched_at,
            created_at=stored.created_at,
            updated_at=stored.updated_at,
        )

    async def get_post_by_link(self, message_link: str):
        if self._stored_post.message_link == message_link:
            return await self.get_post(self._stored_post.post_id)
        return None

    async def update_post(self, post_id, update_data):
        for key, value in update_data.items():
            if key in {"updated_at", "content_fetched_at"} and isinstance(value, str):
                setattr(self._stored_post, key, Timestamp(value))
            else:
                setattr(self._stored_post, key, value)
        return True


@pytest.mark.asyncio
async def test_mass_validate_posts_serializes_concurrent_validations(monkeypatch):
    # Reset global locks to avoid cross-test loop reuse
    monkeypatch.setattr(post_module, "_validation_lock_registry", {})
    monkeypatch.setattr(post_module, "_registry_lock", None)

    template_post = Post(message_link="https://t.me/channel/1", post_id=1)
    stored_post = Post(message_link=template_post.message_link, post_id=template_post.post_id)
    fake_db = _FakeDB(stored_post)
    monkeypatch.setattr("main_logic.database.get_db", lambda: fake_db)

    client = _DummyClient()
    posts_run_one = [Post(message_link=template_post.message_link, post_id=template_post.post_id)]
    posts_run_two = [Post(message_link=template_post.message_link, post_id=template_post.post_id)]

    call_counter = {"count": 0}

    async def fake_validate(self, client, logger=None):
        call_counter["count"] += 1
        await asyncio.sleep(0.05)
        self.chat_id = 777
        self.message_id = 555
        self.updated_at = Timestamp.now()
        await fake_db.update_post(self.post_id, {
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "updated_at": str(self.updated_at),
        })
        return self

    monkeypatch.setattr(Post, "validate", fake_validate)

    results = await asyncio.gather(
        Post.mass_validate_posts(posts_run_one, [client]),
        Post.mass_validate_posts(posts_run_two, [client])
    )

    assert call_counter["count"] == 1, "Validation should run only once across concurrent tasks"
    for result in results:
        assert len(result) == 1
        assert result[0].is_validated
        assert result[0].chat_id == 777
        assert result[0].message_id == 555
