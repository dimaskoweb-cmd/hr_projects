import datetime as dt
import json
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import checko_fetch as cf  # noqa: E402

KEY = "SECRET-KEY-123"
TODAY = dt.date(2026, 10, 8)


def company_ok(**over):
    data = {
        "ОГРН": "1027700198767", "ИНН": "7707049388", "НаимСокр": "ООО «Тест»",
        "ДатаРег": "2015-03-10", "Статус": {"Код": "001", "Наим": "Действует"},
        "Регион": {"Наим": "Москва"}, "ОКВЭД": {"Наим": "Торговля оптовая"},
        "УстКап": {"Сумма": 10000}, "СЧР": 25, "ЮрАдрес": {"МассАдрес": []},
        "Налоги": {"СумУпл": 1500000.0, "СведУплГод": "2025", "СумНедоим": 0},
    }
    data.update(over)
    return {"data": data, "meta": {"status": "ok", "today_request_count": 1, "balance": 0.0}}


def rel_ok():
    return {"data": {"Масштаб": 55, "Номинальность": {"Уровень": 1, "Наим": "Риск не выявлен"},
                     "ФинРиски": {"Уровень": 2, "Наим": "Низкие"},
                     "Баланс": {"Полож": 60, "ТрВним": 30, "Негатив": 10},
                     "Факторы": [{"Код": "a", "Тип": "Негатив", "Вес": 2.0, "Текст": "Есть долги"},
                                 {"Код": "b", "Тип": "Полож", "Вес": 1.0, "Текст": "Давно работает"}]},
            "meta": {"status": "ok", "today_request_count": 2, "balance": 0.0}}


def make_fetch(responses, calls=None):
    def fetch(url):
        if calls is not None:
            calls.append(url)
        method = url.split("/v2/")[1].split("?")[0]
        return responses[method]
    return fetch


def args(inn="7707049388", ogrn=None, extra=False):
    return types.SimpleNamespace(inn=inn, ogrn=ogrn, extra=extra)


class InnTests(unittest.TestCase):
    def test_valid_examples_from_docs(self):
        self.assertTrue(cf.valid_inn("7707049388"))
        self.assertTrue(cf.valid_inn("4027148080"))

    def test_invalid(self):
        for bad in ("7707049389", "123", "77070493881", "abcdefghij", "", None):
            self.assertFalse(cf.valid_inn(bad), bad)


class KeyTests(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.pop("CHECKO_API_KEY", None)

    def tearDown(self):
        os.environ.pop("CHECKO_API_KEY", None)
        if self._old is not None:
            os.environ["CHECKO_API_KEY"] = self._old

    def test_env_file_with_bom_quotes_and_comments(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, ".env"), "w", encoding="utf-8-sig") as f:
                f.write('# comment\nOTHER=1\nCHECKO_API_KEY="abc123"\n')
            self.assertEqual(cf.load_key([d]), "abc123")

    def test_env_txt_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, ".env.txt"), "w", encoding="utf-8") as f:
                f.write("CHECKO_API_KEY=winkey\n")
            self.assertEqual(cf.load_key([d]), "winkey")

    def test_environment_wins(self):
        os.environ["CHECKO_API_KEY"] = "fromenv"
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, ".env"), "w") as f:
                f.write("CHECKO_API_KEY=fromfile\n")
            self.assertEqual(cf.load_key([d]), "fromenv")

    def test_missing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(cf.load_key([d]))


class CallTests(unittest.TestCase):
    def test_url_has_key_and_inn(self):
        calls = []
        ok, _, err = cf.call("company", KEY, {"inn": "7707049388"}, make_fetch({"company": (200, company_ok())}, calls))
        self.assertTrue(ok)
        self.assertIn("key=%s" % KEY, calls[0])
        self.assertIn("inn=7707049388", calls[0])

    def test_api_error_message_and_no_key_leak(self):
        resp = (403, {"meta": {"status": "error", "message": "bad key %s" % KEY}})
        ok, _, err = cf.call("company", KEY, {"inn": "7707049388"}, make_fetch({"company": resp}))
        self.assertFalse(ok)
        self.assertIn("HTTP 403", err)
        self.assertNotIn(KEY, err)

    def test_network_error_redacts_key(self):
        def boom(url):
            raise OSError("cannot reach " + url)
        ok, data, err = cf.call("company", KEY, {"inn": "7707049388"}, boom)
        self.assertFalse(ok)
        self.assertIsNone(data)
        self.assertNotIn(KEY, err)


class CardTests(unittest.TestCase):
    def test_card_contents(self):
        card = cf.build_card(company_ok(), rel_ok(), {}, TODAY)
        for s in ("ООО «Тест»", "Действует", "11 г. 6 мес.", "Масштаб бизнеса: 55 из 100",
                  "негативных 10%", "негатив: Есть долги", "СТОП-ФАКТОРЫ: не выявлены",
                  "10 000 руб."):
            self.assertIn(s, card)
        self.assertNotIn("Давно работает", card)  # положительные факторы в карточку не выводим

    def test_missing_blocks_marked(self):
        card = cf.build_card(None, None, {}, TODAY)
        self.assertIn("нет данных", card)
        self.assertNotIn("не выявлены", card)

    def test_stop_liquidation(self):
        c = company_ok(Статус={"Наим": "Находится в процессе ликвидации"})["data"]
        self.assertTrue(cf.stop_factors(c, 0, TODAY))

    def test_stop_young_mass_address_and_director(self):
        c = company_ok(ДатаРег="2026-03-01", МассРуковод=True, ЮрАдрес={"МассАдрес": ["111"]})["data"]
        self.assertTrue(any("младше года" in s for s in cf.stop_factors(c, 0, TODAY)))

    def test_old_company_with_mass_address_is_not_stop(self):
        c = company_ok(МассРуковод=True, ЮрАдрес={"МассАдрес": ["111"]})["data"]
        self.assertEqual(cf.stop_factors(c, 0, TODAY), [])

    def test_stop_bankruptcy_sanctions_disq(self):
        c = company_ok(Санкции=True, ДисквЛица=True, НедобПост=True)["data"]
        self.assertEqual(len(cf.stop_factors(c, 2, TODAY)), 4)


class RunTests(unittest.TestCase):
    def test_run_writes_files_without_key(self):
        with tempfile.TemporaryDirectory() as d:
            fetch = make_fetch({"company": (200, company_ok()), "reliability": (200, rel_ok())})
            text, errs = cf.run(args(), KEY, fetch, TODAY, d)
            self.assertFalse(errs)
            files = sorted(os.listdir(d))
            self.assertEqual(files, ["7707049388_2026-10-08.json", "7707049388_2026-10-08.txt"])
            for fn in files:
                with open(os.path.join(d, fn), encoding="utf-8") as fh:
                    content = fh.read()
                self.assertNotIn(KEY, content)
                if fn.endswith(".json"):
                    json.loads(content)
            self.assertIn("Запросов за сегодня: 2", text)

    def test_default_run_makes_two_requests(self):
        calls = []
        fetch = make_fetch({"company": (200, company_ok()), "reliability": (200, rel_ok())}, calls)
        cf.run(args(), KEY, fetch, TODAY)
        self.assertEqual(len(calls), 2)

    def test_extra_makes_seven_requests_and_reports_partial_failure(self):
        calls = []
        resp = {m: (200, {"data": {}, "meta": {"status": "ok"}}) for m in cf.EXTRA_METHODS}
        resp.update({"company": (200, company_ok()), "reliability": (200, rel_ok()),
                     "inspections": (400, {"meta": {"status": "error", "message": "неверные параметры"}})})
        text, errs = cf.run(args(extra=True), KEY, make_fetch(resp, calls), TODAY)
        self.assertEqual(len(calls), 7)
        self.assertTrue(errs)
        self.assertIn("ОШИБКИ", text)
        self.assertIn("inspections", text)

    def test_reliability_failure_keeps_company_card(self):
        resp = {"company": (200, company_ok()),
                "reliability": (404, {"meta": {"status": "error", "message": "организация не действует"}})}
        text, errs = cf.run(args(), KEY, make_fetch(resp), TODAY)
        self.assertTrue(errs)
        self.assertIn("ООО «Тест»", text)
        self.assertIn("Надёжность: нет данных", text)

    def test_ogrn_mode_uses_ogrn_param(self):
        calls = []
        fetch = make_fetch({"company": (200, company_ok()), "reliability": (200, rel_ok())}, calls)
        cf.run(args(inn=None, ogrn="1027700198767"), KEY, fetch, TODAY)
        self.assertTrue(all("ogrn=1027700198767" in u and "inn=" not in u for u in calls))


if __name__ == "__main__":
    unittest.main()
