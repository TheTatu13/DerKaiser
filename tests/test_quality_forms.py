from scraper import quality as Q

def _rec(forma, name="X"):
    return {"name": name, "stare": "INREGISTRAT din data 01.01.2000", "forma_juridica": forma, "caen": "1011", "inactive": False}

def test_cooperatives_and_other_legal_forms_are_admitted():
    assert Q.assess_anaf(_rec("ORGANIZATIE COOPERATISTA MESTESUGAREASCA")) == []
    assert Q.assess_anaf(_rec("ORGANIZATIE COOPERATISTA DE CONSUM")) == []
    assert Q.assess_anaf(_rec("ALTE FORME JURIDICE", "INSTITUTUL NATIONAL DE CERCETARE")) == []

def test_sole_traders_are_still_rejected():
    assert Q.assess_anaf(_rec("ALTE FORME JURIDICE", "POPESCU ION PFA")) == ["name_sole_trader"]
    assert Q.assess_anaf(_rec("PERSOANA FIZICA AUTORIZATA")) != []
