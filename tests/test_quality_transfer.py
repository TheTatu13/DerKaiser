from scraper import quality as Q


def test_transfer_sosire_is_an_active_firm():
    rec = {"name": "CATENA PHARMA S.R.L.", "stare": "TRANSFER(SOSIRE) din data 01.01.2020",
           "forma_juridica": "SOCIETATE COMERCIALA CU RASPUNDERE LIMITATA", "caen": "4646", "inactive": False}
    assert Q.assess_anaf(rec) == []
