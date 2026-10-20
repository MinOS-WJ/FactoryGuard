import pytest

from main import main


def test_unified_main_dispatches_to_simulation(capsys):
    assert main(["simulate", "--frames", "1"]) == 0
    assert '"frames": 1' in capsys.readouterr().out


def test_gui_rejects_non_finite_fps():
    with pytest.raises(SystemExit):
        main(["gui", "--fps", "nan"])
