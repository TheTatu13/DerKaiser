import gzip
import json

from scraper import cc

LD = ('<html><script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",'
      '"title":"Contabil","hiringOrganization":{"name":"Acme"},"jobLocation":{"@type":"Place","address":'
      '{"addressLocality":"Pitesti","addressCountry":"RO"}},"description":"x"}</script></html>')


def _nosize(_u):
    raise OSError('offline')


def warc(html):
    body = b"WARC/1.0\r\nWARC-Type: response\r\n\r\nHTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n" + html.encode()
    return gzip.compress(body)


def test_cdx_url_and_records():
    u = cc.cdx_url("CC-MAIN-2026-43", "acme.ro")
    assert u.startswith("https://index.commoncrawl.org/CC-MAIN-2026-43-index?") and "acme.ro%2F%2A" in u
    line = json.dumps({"url": "https://acme.ro/cariere/contabil", "filename": "f.warc.gz", "offset": "10", "length": "99"})
    assert len(cc.cdx_records(lambda _u: line + "\nnot json\n", "C", "acme.ro")) == 1


def test_warc_body_and_sweep():
    raw = warc(LD)
    assert "JobPosting" in cc.warc_body(raw)
    line = json.dumps({"url": "https://acme.ro/cariere/contabil", "filename": "f.warc.gz", "offset": "0", "length": str(len(raw))})
    firms = [{"cif": "1", "company": "Acme", "site": "https://www.acme.ro"}]
    alive = cc.sweep(firms, lambda u: line, lambda u, a, b: raw, lambda u: 200, crawls=["C"], size_of=_nosize)
    assert len(alive) == 1 and alive[0]["cif"] == "1" and alive[0]["job_title"] == "Contabil"
    assert cc.sweep(firms, lambda u: line, lambda u, a, b: raw, lambda u: 404, crawls=["C"], size_of=_nosize) == []
    assert cc.domain_of("https://www.acme.ro/x") == "acme.ro" and cc.domain_of(None) is None


def test_index_failure_is_not_fatal():
    def boom(_u):
        raise TimeoutError()
    firms = [{"cif": "1", "company": "Acme", "site": "acme.ro"}]
    assert cc.sweep(firms, boom, None, lambda u: 200, crawls=["C"], size_of=_nosize) == []


def test_malformed_site():
    assert cc.domain_of("http://[bad") is None


def _fake_index(n_filler=3000):
    """cluster.idx with many filler keys around ro,valrom + one cdx block holding valrom pages."""
    page = {"url": "https://www.valrom.ro/joburi/reglor", "status": "200", "mime": "text/html",
            "filename": "crawl-data/x.warc.gz", "offset": "5", "length": "7"}
    other = {"url": "https://other.ro/cariere/x", "status": "200", "mime": "text/html", "filename": "f", "offset": "1", "length": "1"}
    block = gzip.compress(("ro,other)/cariere/x 20261001 " + json.dumps(other) + "\n"
                           "ro,valrom,www)/joburi/reglor 20261001 " + json.dumps(page) + "\n").encode())
    keys = [f"ro,a{i:05d})/ 20261001" for i in range(n_filler)] + ["ro,other)/ 20261001"] \
        + [f"ro,z{i:05d})/ 20261001" for i in range(n_filler)]
    lines, files, pos = [], {}, 0
    for i, k in enumerate(keys):
        if k.startswith("ro,other)"):
            files["cdx-00001.gz"] = block
            lines.append(f"{k}\tcdx-00001.gz\t0\t{len(block)}\t{i}")
        else:
            lines.append(f"{k}\tcdx-00000.gz\t0\t10\t{i}")
    idx = ("\n".join(lines) + "\n").encode()
    return idx, block


def test_local_index_finds_domain_block():
    idx, block = _fake_index()

    def get_range(url, a, b):
        if url.endswith("cluster.idx"):
            return idx[a:b + 1]
        assert url.endswith("cdx-00001.gz")
        return block[a:b + 1]

    recs = cc.local_records(get_range, "CC-X", "valrom.ro", len(idx))
    assert [r["url"] for r in recs] == ["https://www.valrom.ro/joburi/reglor"]
    assert cc.local_records(get_range, "CC-X", "absent.ro", len(idx)) == []
    assert cc.surt_host("www.acme.ro") == "ro,acme,www"


def test_site_as_list():
    assert cc.domain_of(["https://www.icredit.ro"]) == "icredit.ro" and cc.domain_of([]) is None
