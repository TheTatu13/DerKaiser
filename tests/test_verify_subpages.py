from scraper import model as M


def test_cif_found_on_contact_page_verifies_guessed_site():
    pages = {"https://adarom.ro": "<html>Welcome</html>",
             "https://adarom.ro/contact": "<p>ADAROM TRANS SRL, CUI: RO 1234567, Pitesti</p>"}
    def get(u):
        if u in pages: return pages[u]
        raise OSError("404")
    assert M.verify_site("adarom.ro", "ADAROM TRANS SRL", get, strict_cif="01234567") == "https://adarom.ro"


def test_other_firms_cif_on_contact_page_is_rejected():
    pages = {"https://adarom.ro": "<html>Welcome</html>", "https://adarom.ro/contact": "<p>CUI 7654321</p>"}
    def get(u):
        if u in pages: return pages[u]
        raise OSError("404")
    assert M.verify_site("adarom.ro", "ADAROM TRANS SRL", get, strict_cif="01234567") is None


def test_bare_number_without_cif_label_does_not_verify():
    pages = {"https://adarom.ro": "<html>Welcome</html>", "https://adarom.ro/contact": "<p>Pret 1234567 lei</p>"}
    def get(u):
        if u in pages: return pages[u]
        raise OSError("404")
    assert M.verify_site("adarom.ro", "ADAROM TRANS SRL", get, strict_cif="01234567") is None
