from scraper import engine, workday as WD

HOME = '<a href="https://acme.wd3.myworkdayjobs.com/en-US/AcmeCareers">Cariere</a>'


def _page(n, total, start):
    return {"total": total, "jobPostings": [
        {"title": f"Inginer {start + i}", "externalPath": f"/job/Bucharest/Inginer-{start + i}_R{start + i}",
         "locationsText": "Bucharest, Romania"} for i in range(n)]}


def test_detect():
    assert WD.detect_workday(HOME) == ("acme", "wd3", "AcmeCareers")
    assert WD.detect_workday("https://x.wd5.myworkdayjobs.com/wday/cxs/x/y/jobs") is None
    assert WD.detect_workday("nothing") is None


def test_parse_keeps_only_romania_and_builds_link():
    data = {"jobPostings": [
        {"title": "Dev", "externalPath": "/job/Romania-Remote/Dev_1", "locationsText": "2 Locations"},
        {"title": "Dev US", "externalPath": "/job/Austin/Dev_2", "locationsText": "Austin, United States"}]}
    jobs = WD.parse_workday("Acme", "acme", "wd3", "AcmeCareers", data)
    assert [j["job_title"] for j in jobs] == ["Dev"]
    assert jobs[0]["job_link"] == "https://acme.wd3.myworkdayjobs.com/AcmeCareers/job/Romania-Remote/Dev_1"


def test_pagination_uses_first_page_total():
    calls = []

    def post(url, body):
        calls.append(body["offset"])
        if body["offset"] == 0:
            return _page(20, 45, 0)
        return {"total": 0, "jobPostings": _page(20 if body["offset"] == 20 else 5, 0, body["offset"])["jobPostings"]}

    jobs = WD.fetch_workday("Acme", "acme", "wd3", "AcmeCareers", post)
    assert calls == [0, 20, 40] and len(jobs) == 45


def test_engine_layer_uses_workday(tmp_path):
    pages = {"https://acme.ro": '<a href="/cariere">c</a>', "https://acme.ro/cariere": HOME}
    posts = []

    def fetch(url):
        if url in pages:
            return pages[url]
        raise OSError(url)

    def post(url, body):
        posts.append(url)
        return _page(3, 3, 0)

    res = engine.run_firm("1", "Acme", "https://acme.ro", [], fetch=fetch, status=lambda u: 200, post=post)
    assert res["method"] == "workday" and len(res["jobs"]) == 3 and posts
