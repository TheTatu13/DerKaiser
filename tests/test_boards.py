import json

from scraper import boards as B


def test_fetch_boards_keeps_romania_and_survives_errors():
    gh = {"jobs": [{"title": "QA Engineer", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
                    "location": {"name": "Bucharest, Romania"}},
                   {"title": "QA Engineer", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/2",
                    "location": {"name": "Berlin, Germany"}}]}

    def get(url):
        if "acme" in url:
            return json.dumps(gh)
        raise OSError("down")

    cfg = {"greenhouse": [{"company": "Acme", "slug": "acme"}], "lever": [{"company": "Bad", "slug": "bad"}]}
    seen = []
    jobs = B.fetch_boards(cfg, get, lambda u, b: {}, on_board=lambda k, c, n: seen.append((k, c, n)))
    assert [j["job_link"] for j in jobs] == ["https://job-boards.greenhouse.io/acme/jobs/1"]
    assert jobs[0]["source"] == "ats:greenhouse" and ("lever", "Bad", 0) in seen


def test_workday_board_uses_post():
    data = {"total": 1, "jobPostings": [{"title": "Analyst", "externalPath": "/job/Bucharest/Analyst_R1",
                                        "locationsText": "Bucharest"}]}
    cfg = {"workday": [{"company": "Ing", "tenant": "ing", "wd": "wd3", "site": "S"}]}
    jobs = B.fetch_boards(cfg, lambda u: "", lambda u, b: data)
    assert len(jobs) == 1 and jobs[0]["source"] == "ats:workday"
