import pytest

from comicsbot.lock import AlreadyRunning, InstanceLock


def test_second_instance_is_refused(tmp_path):
    path = tmp_path / "bot.lock"
    with InstanceLock(path):
        with pytest.raises(AlreadyRunning):
            InstanceLock(path).acquire()
    # Released again once the first holder is done.
    with InstanceLock(path):
        pass
