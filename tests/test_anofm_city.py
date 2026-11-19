from scraper import model as M


def test_best_city_bucharest_sector_and_municipality():
    assert M.best_city(["Municipiul București", "MUNICIPIUL BUCURESTI", "BUCURESTI SECT 1"]).lower().startswith("bucure")
    assert M.best_city(["Argeș", "Municipiul Pitești", "PITESTI"]).lower().startswith("pite")
    assert M.best_city(["Cluj", "Municipiul Cluj-Napoca", "CLUJ-NAPOCA"]) == "Cluj-Napoca"


def test_best_city_unknown_village_falls_back_empty():
    assert M.best_city(["Argeș", "Comuna Bascov", "BASCOV"]) == ""


def test_parse_anofm_uses_the_best_city():
    row = {"id": "1", "occupation": "X", "address_locality_name": "Municipiul București > MUNICIPIUL BUCURESTI > BUCURESTI SECT 1"}
    job = M.parse_anofm({"rows": [row]})[0]
    assert job["location"][0].lower().startswith("bucure")
