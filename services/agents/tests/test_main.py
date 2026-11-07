import json

from tm_agents.__main__ import main


def test_main_prints_health(capsys):
    main()
    out = json.loads(capsys.readouterr().out)
    assert out["service"] == "agents"
