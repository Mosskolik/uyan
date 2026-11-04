#!/usr/bin/env python3
"""
UYAN - Uyku / goz kapanma algilayici alarm.

Kamerayi izler; gozlerin belirli bir sureden uzun kapali kalirsa, basin
one/arkaya duserse ya da yuzun kameradan kaybolursa kulakliktan /
hoparlorden gittikce yukselen bir alarm calar. Gozlerini birkac saniye
acik tuttugunda alarm kendiliginden susar.

Kullanim:
    python uyan.py                 # varsayilan: 3 sn goz kapali -> alarm
    python uyan.py --sure 5        # 5 sn
    python uyan.py --yuz-yok 15    # yuz 15 sn gorunmezse alarm (vars. 10, 0 = kapali)
    python uyan.py --ses alarm.mp3 # kendi alarm sesin

Tuslar / dugmeler (kamera penceresi seciliyken):
    KAPAT dugmesi / q / ESC : cikis
    SUSTUR dugmesi / BOSLUK : alarmi sustur
    c                       : yeniden kalibre et
"""

import argparse
import math
import os
import statistics
import sys
import threading
import time
import urllib.request

import cv2
import numpy as np

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)
# .exe olarak paketlendiyse modeli exe'nin yanina kaydet
_KLASOR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
MODEL_PATH = os.path.join(_KLASOR, "face_landmarker.task")

# MediaPipe yuz agindaki goz noktalari (p1..p6, EAR formulu icin)
SOL_GOZ = [362, 385, 387, 263, 373, 380]
SAG_GOZ = [33, 160, 158, 133, 153, 144]

# Anlik titremeleri (gozluk parlamasi, tek karelik hata) yok saymak icin sureler
GOZ_ACIK_TOLERANS = 0.6   # gozler en az bu kadar acik kalmazsa "kapali" sayaci sifirlanmaz
YUZ_VAR_TOLERANS = 1.0    # yuz en az bu kadar gorunmezse "yuz yok" sayaci sifirlanmaz

KAL_HAZIRLIK = 2.0        # kalibrasyon oncesi hazirlan suresi
KAL_OLCUM = 3.0           # kalibrasyon olcum suresi (geri sayim)
KAL_SONUC = 2.5           # sonucun ekranda kalma suresi


# --------------------------------------------------------------------------
# Ses
# --------------------------------------------------------------------------
def _ton(frekanslar, sure, sr=44100, kare_dalga=False, ses=0.8, bosluk=0.05):
    parcalar = []
    for frek in frekanslar:
        t = np.linspace(0, sure, int(sr * sure), endpoint=False)
        if kare_dalga:
            dalga = np.sign(np.sin(2 * np.pi * frek * t)) * 0.6 + np.sin(2 * np.pi * frek * 2 * t) * 0.4
        else:
            dalga = np.sin(2 * np.pi * frek * t)
        zarf = np.minimum(1, np.minimum(t, t[-1] - t) * 200)  # tik sesini onle
        parcalar.append(dalga * zarf)
        parcalar.append(np.zeros(int(sr * bosluk)))
    mono = np.concatenate(parcalar)
    stereo = np.column_stack([mono, mono])
    return np.ascontiguousarray((stereo * 32767 * ses).astype(np.int16))


class Alarm:
    """Varsayilan ses cikisindan (kulaklik takiliysa kulakliktan) alarm ve bildirim sesleri calar."""

    BILDIRIMLER = {
        "basla": ([660], 0.15),
        "tik": ([880], 0.07),
        "basarili": ([660, 880, 1320], 0.12),
        "basarisiz": ([330, 220], 0.3),
    }

    def __init__(self, ses_dosyasi=None, max_ses=1.0):
        self.max_ses = max_ses
        self.aktif = False
        self._thread = None
        self._dur = threading.Event()
        self.pygame = None
        self.ses = None
        self.bildirim_sesleri = {}
        try:
            import pygame

            pygame.mixer.init(frequency=44100, size=-16, channels=2)
            self.pygame = pygame
            if ses_dosyasi:
                self.ses = pygame.mixer.Sound(ses_dosyasi)
            else:
                self.ses = pygame.sndarray.make_sound(_ton((880, 1320, 880, 1320), 0.18, kare_dalga=True, ses=0.9, bosluk=0.07))
            for ad, (frek, sure) in self.BILDIRIMLER.items():
                self.bildirim_sesleri[ad] = pygame.sndarray.make_sound(_ton(frek, sure, ses=0.5))
        except Exception as e:  # pygame yoksa ya da ses aygiti acilamazsa
            print(f"[uyari] pygame ile ses acilamadi ({e}). Yedek bip kullanilacak.")
            self.pygame = None

    def bildirim(self, ad):
        """Kisa bilgi sesi (kalibrasyon basladi / basarili / basarisiz...)."""
        if self.aktif:
            return
        if self.pygame and ad in self.bildirim_sesleri:
            self.bildirim_sesleri[ad].play()
        elif sys.platform.startswith("win"):
            frek, sure = self.BILDIRIMLER.get(ad, ([800], 0.1))

            def cal():
                import winsound

                for f in frek:
                    winsound.Beep(int(f), int(sure * 1000))

            threading.Thread(target=cal, daemon=True).start()

    def baslat(self):
        if self.aktif:
            return
        self.aktif = True
        self._dur.clear()
        self._thread = threading.Thread(target=self._calis, daemon=True)
        self._thread.start()

    def durdur(self):
        if not self.aktif:
            return
        self.aktif = False
        self._dur.set()
        if self.pygame:
            self.pygame.mixer.stop()

    def _calis(self):
        baslangic = time.time()
        if self.pygame:
            kanal = self.ses.play(loops=-1)
            while not self._dur.is_set():
                # %30'dan baslayip ~10 sn icinde en yuksek sese cik
                gecen = time.time() - baslangic
                seviye = min(self.max_ses, 0.3 + 0.07 * gecen)
                if kanal is not None:
                    kanal.set_volume(seviye)
                self._dur.wait(0.2)
            if kanal is not None:
                kanal.stop()
        else:
            while not self._dur.is_set():
                if sys.platform.startswith("win"):
                    import winsound

                    winsound.Beep(1000, 300)
                    winsound.Beep(1500, 300)
                else:
                    sys.stdout.write("\a")
                    sys.stdout.flush()
                    self._dur.wait(0.5)


# --------------------------------------------------------------------------
# Goz algilayicilar
# --------------------------------------------------------------------------
def ear_hesapla(noktalar):
    """Goz acikligi orani (Eye Aspect Ratio). Acik goz ~0.25-0.35, kapali ~0.1."""
    p1, p2, p3, p4, p5, p6 = noktalar
    d = lambda a, b: math.dist(a, b)
    yatay = d(p1, p4)
    if yatay == 0:
        return 0.0
    return (d(p2, p6) + d(p3, p5)) / (2.0 * yatay)


def bos_olcum():
    return {"yuz": False, "ear": None, "kirpma": None, "egim": None, "noktalar": []}


class MediaPipeAlgilayici:
    """MediaPipe FaceLandmarker ile goz takibi.

    Iki ayri olcum kullanir:
      * EAR: goz kapaklari arasi mesafe (geometrik)
      * kirpma: modelin kendi "goz kirpma" skoru (0 = acik, 1 = kapali).
        Gozluk parlamasindan EAR'a gore cok daha az etkilenir.
    Ayrica basin one/arkaya egimini (derece) olcer.
    """

    ad = "MediaPipe"
    kalibrasyon_gerekli = True

    def __init__(self):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        if not os.path.exists(MODEL_PATH):
            print("Yuz modeli indiriliyor (tek seferlik, ~4 MB)...")
            gecici = MODEL_PATH + ".part"
            urllib.request.urlretrieve(MODEL_URL, gecici)
            os.replace(gecici, MODEL_PATH)

        self.mp = mp
        secenekler = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=MODEL_PATH),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.6,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
        self.model = vision.FaceLandmarker.create_from_options(secenekler)
        self._son_ts = 0

    def kapat(self):
        try:
            self.model.close()
        except Exception:
            pass

    def olc(self, kare):
        h, w = kare.shape[:2]
        rgb = cv2.cvtColor(kare, cv2.COLOR_BGR2RGB)
        goruntu = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
        ts = max(self._son_ts + 1, int(time.monotonic() * 1000))
        self._son_ts = ts
        sonuc = self.model.detect_for_video(goruntu, ts)
        olcum = bos_olcum()
        if not sonuc.face_landmarks:
            return olcum
        lm = sonuc.face_landmarks[0]
        sol = [(lm[i].x * w, lm[i].y * h) for i in SOL_GOZ]
        sag = [(lm[i].x * w, lm[i].y * h) for i in SAG_GOZ]
        olcum["yuz"] = True
        olcum["ear"] = (ear_hesapla(sol) + ear_hesapla(sag)) / 2.0
        olcum["noktalar"] = sol + sag
        if sonuc.face_blendshapes:
            skorlar = {k.category_name: k.score for k in sonuc.face_blendshapes[0]}
            if "eyeBlinkLeft" in skorlar and "eyeBlinkRight" in skorlar:
                olcum["kirpma"] = (skorlar["eyeBlinkLeft"] + skorlar["eyeBlinkRight"]) / 2.0
        if sonuc.facial_transformation_matrixes:
            R = np.array(sonuc.facial_transformation_matrixes[0])[:3, :3]
            olcum["egim"] = float(np.degrees(np.arctan2(R[2, 1], R[2, 2])))
        return olcum


class HaarAlgilayici:
    """Yedek yontem: OpenCV Haar kaskatlari (internet/model gerektirmez, daha az hassas)."""

    ad = "OpenCV Haar (yedek)"
    kalibrasyon_gerekli = False

    def __init__(self):
        yol = cv2.data.haarcascades
        self.yuz = cv2.CascadeClassifier(os.path.join(yol, "haarcascade_frontalface_default.xml"))
        self.goz = cv2.CascadeClassifier(os.path.join(yol, "haarcascade_eye_tree_eyeglasses.xml"))

    def kapat(self):
        pass

    def olc(self, kare):
        olcum = bos_olcum()
        gri = cv2.equalizeHist(cv2.cvtColor(kare, cv2.COLOR_BGR2GRAY))
        yuzler = self.yuz.detectMultiScale(gri, 1.2, 5, minSize=(100, 100))
        if len(yuzler) == 0:
            return olcum
        x, y, w, h = max(yuzler, key=lambda r: r[2] * r[3])
        ust = gri[y : y + h // 2, x : x + w]
        gozler = self.goz.detectMultiScale(ust, 1.1, 4, minSize=(w // 10, w // 10))
        olcum["yuz"] = True
        olcum["noktalar"] = [(x + gx + gw / 2, y + gy + gh / 2) for gx, gy, gw, gh in gozler]
        # Acik goz bulunduysa 0.3, bulunamadiysa 0.0 -> EAR'a benzer bir olcek
        olcum["ear"] = 0.3 if len(gozler) > 0 else 0.0
        return olcum


def algilayici_olustur(zorla_haar=False):
    if not zorla_haar:
        try:
            return MediaPipeAlgilayici()
        except Exception as e:
            print(f"[uyari] MediaPipe kullanilamadi ({e}). Yedek yontem kullaniliyor.")
    return HaarAlgilayici()


# --------------------------------------------------------------------------
# Kalibrasyon
# --------------------------------------------------------------------------
class Kalibrasyon:
    """Hazirlik -> 3-2-1 geri sayimla olcum -> basarili / basarisiz sonucu."""

    def __init__(self, alarm):
        self.alarm = alarm
        self.esikler = None       # basariliysa: {"ear", "kirpma", "egim"}
        self.basla()

    def basla(self):
        self.durum = "hazirlik"
        self.bas = time.time()
        self.ornek = []
        self.kare_sayisi = 0
        self.son_saniye = None
        self.mesaj = ""
        self.basarili = False
        self.alarm.bildirim("basla")
        print("Kalibrasyon basliyor: gozlerin ACIK, duz kameraya bak...")

    @property
    def aktif(self):
        return self.durum is not None

    def guncelle(self, olcum, simdi):
        gecen = simdi - self.bas
        if self.durum == "hazirlik":
            if gecen >= KAL_HAZIRLIK:
                self.durum, self.bas = "olcum", simdi
        elif self.durum == "olcum":
            self.kare_sayisi += 1
            if olcum["yuz"]:
                self.ornek.append(olcum)
            kalan = math.ceil(KAL_OLCUM - gecen)
            if kalan != self.son_saniye and kalan > 0:
                self.son_saniye = kalan
                self.alarm.bildirim("tik")
            if gecen >= KAL_OLCUM:
                self._degerlendir()
                self.durum, self.bas = "sonuc", simdi
        elif self.durum == "sonuc":
            if gecen >= (KAL_SONUC if self.basarili else KAL_SONUC + 1):
                if self.basarili:
                    self.durum = None
                else:
                    self.basla()  # basarisizsa kendiliginden tekrar dene

    def _degerlendir(self):
        oran = len(self.ornek) / max(1, self.kare_sayisi)
        ear = [o["ear"] for o in self.ornek if o["ear"] is not None]
        kirpma = [o["kirpma"] for o in self.ornek if o["kirpma"] is not None]
        egim = [o["egim"] for o in self.ornek if o["egim"] is not None]

        hata = None
        if self.kare_sayisi < 5 or oran < 0.6 or len(ear) < 5:
            hata = "Yuz yeterince gorunmedi - kameraya donup tekrar dene"
        else:
            ear_ort = statistics.median(ear)
            kirpma_ort = statistics.median(kirpma) if kirpma else None
            if (kirpma_ort is not None and kirpma_ort > 0.5) or ear_ort < 0.12:
                hata = "Gozler kapali gorundu - gozlerini ACIK tut"
            elif len(egim) > 3 and statistics.pstdev(egim) > 8:
                hata = "Cok hareket ettin - basini sabit tut"

        if hata:
            self.basarili = False
            self.mesaj = hata
            self.alarm.bildirim("basarisiz")
            print("Kalibrasyon BASARISIZ:", hata)
            return

        self.basarili = True
        self.esikler = {
            "ear": ear_ort * 0.70,
            # acik gozdeki kirpma skorunun belirgin ustu = kapali
            "kirpma": min(0.8, max(0.45, (kirpma_ort or 0) + 0.4)) if kirpma else None,
            "egim": statistics.median(egim) if egim else None,
        }
        self.mesaj = "Artik izleniyorsun, iyi calismalar!"
        self.alarm.bildirim("basarili")
        print(
            f"Kalibrasyon BASARILI. acik goz EAR={ear_ort:.3f} -> esik {self.esikler['ear']:.3f}"
            + (f", kirpma esigi {self.esikler['kirpma']:.2f}" if self.esikler["kirpma"] else "")
        )

    def ciz(self, kare, simdi):
        h, w = kare.shape[:2]
        gecen = simdi - self.bas
        karart = np.zeros_like(kare)
        kare[:] = cv2.addWeighted(kare, 0.55, karart, 0.45, 0)
        if self.durum == "hazirlik":
            orta_yazi(kare, "KALIBRASYON BASLIYOR", h // 2 - 40, (0, 255, 255), 1.1, 3)
            orta_yazi(kare, "Gozlerin ACIK, duz kameraya bak", h // 2 + 10, (255, 255, 255), 0.75, 2)
        elif self.durum == "olcum":
            kalan = max(1, math.ceil(KAL_OLCUM - gecen))
            orta_yazi(kare, "Gozlerini ACIK tut...", 70, (0, 255, 255), 0.9, 2)
            orta_yazi(kare, str(kalan), h // 2 + 50, (0, 255, 255), 5.0, 12)
            # ilerleme cubugu
            oran = min(1.0, gecen / KAL_OLCUM)
            cv2.rectangle(kare, (w // 4, h - 60), (w * 3 // 4, h - 40), (255, 255, 255), 2)
            cv2.rectangle(kare, (w // 4, h - 60), (w // 4 + int(w // 2 * oran), h - 40), (0, 255, 255), -1)
        elif self.durum == "sonuc":
            if self.basarili:
                orta_yazi(kare, "KALIBRASYON BASARILI", h // 2 - 20, (0, 255, 0), 1.1, 3)
                orta_yazi(kare, self.mesaj, h // 2 + 30, (255, 255, 255), 0.65, 2)
            else:
                orta_yazi(kare, "KALIBRASYON BASARISIZ", h // 2 - 40, (0, 0, 255), 1.1, 3)
                orta_yazi(kare, self.mesaj, h // 2 + 10, (255, 255, 255), 0.6, 2)
                orta_yazi(kare, "Tekrar deneniyor...", h // 2 + 50, (0, 255, 255), 0.65, 2)


# --------------------------------------------------------------------------
# Ekran yardimcilari
# --------------------------------------------------------------------------
def yazi(kare, metin, konum, renk=(255, 255, 255), olcek=0.7, kalinlik=2):
    cv2.putText(kare, metin, konum, cv2.FONT_HERSHEY_SIMPLEX, olcek, (0, 0, 0), kalinlik + 3, cv2.LINE_AA)
    cv2.putText(kare, metin, konum, cv2.FONT_HERSHEY_SIMPLEX, olcek, renk, kalinlik, cv2.LINE_AA)


def orta_yazi(kare, metin, y, renk=(255, 255, 255), olcek=0.7, kalinlik=2):
    (tw, _), _ = cv2.getTextSize(metin, cv2.FONT_HERSHEY_SIMPLEX, olcek, kalinlik)
    yazi(kare, metin, (max(5, (kare.shape[1] - tw) // 2), y), renk, olcek, kalinlik)


def buton_ciz(kare, metin, x1, y1, x2, y2, renk):
    """Kareye tiklanabilir bir dugme cizer, koordinatlarini dondurur."""
    cv2.rectangle(kare, (x1, y1), (x2, y2), renk, -1)
    cv2.rectangle(kare, (x1, y1), (x2, y2), (255, 255, 255), 2)
    (tw, th), _ = cv2.getTextSize(metin, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
    cv2.putText(kare, metin, (x1 + (x2 - x1 - tw) // 2, y1 + (y2 - y1 + th) // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
    return (x1, y1, x2, y2)


def gozler_kapali_mi(olcum, esikler, elle_ear_esigi=None):
    """Gozluk parlamasina karsi iki olcumu birlikte kullanir.

    * Modelin kirpma skoru yuksekse -> kapali.
    * EAR esigin altindaysa, ancak kirpma skoru da bir miktar yukseliyorsa -> kapali.
      (Gozluk parlamasi EAR'i dusurur ama kirpma skoru acik gozde dusuk kalir;
       boylece yanlis alarm onlenir.)
    """
    if not olcum["yuz"] or olcum["ear"] is None:
        return False
    ear_esik = elle_ear_esigi if elle_ear_esigi is not None else esikler["ear"]
    ear_kapali = olcum["ear"] < ear_esik
    kirpma, kirpma_esik = olcum["kirpma"], esikler.get("kirpma")
    if kirpma is None or kirpma_esik is None:
        return ear_kapali
    return kirpma >= kirpma_esik or (ear_kapali and kirpma >= 0.25)


# --------------------------------------------------------------------------
# Ana dongu
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Gozlerin kapaninca seni uyandiran alarm.")
    ap.add_argument("--sure", type=float, default=3.0, help="Kac saniye goz kapali kalinca alarm calsin (vars. 3)")
    ap.add_argument("--yuz-yok", type=float, default=10, help="Yuz bu kadar saniye gorunmezse / bas duserse alarm cal (vars. 10, 0 = kapali)")
    ap.add_argument("--bas-acisi", type=float, default=30, help="Bas kalibrasyondakine gore kac derece one/arkaya duserse 'yuz yok' sayilsin (vars. 30, 0 = kapali)")
    ap.add_argument("--uyanik-sure", type=float, default=2.0, help="Alarmin susmasi icin gozlerin kac sn acik kalmali (vars. 2)")
    ap.add_argument("--esik", type=float, default=None, help="Elle goz kapali esigi (EAR, orn. 0.2). Verilmezse kalibrasyondan hesaplanir")
    ap.add_argument("--kamera", type=int, default=0, help="Kamera numarasi (vars. 0)")
    ap.add_argument("--ses", type=str, default=None, help="Alarm icin ses dosyasi (wav/mp3/ogg)")
    ap.add_argument("--ses-seviyesi", type=float, default=1.0, help="En yuksek ses seviyesi 0-1 (vars. 1.0)")
    ap.add_argument("--haar", action="store_true", help="MediaPipe yerine basit OpenCV yontemini kullan")
    ap.add_argument("--pencere-yok", action="store_true", help="Kamera penceresini gosterme (Ctrl+C ile cik)")
    args = ap.parse_args()

    algilayici = algilayici_olustur(args.haar)
    alarm = Alarm(args.ses, max(0.05, min(1.0, args.ses_seviyesi)))

    kamera = cv2.VideoCapture(args.kamera)
    if not kamera.isOpened():
        print("HATA: Kamera acilamadi. Baska bir uygulama kullaniyor olabilir ya da izin verilmemis olabilir.")
        sys.exit(1)

    pencere = "UYAN"
    if not args.pencere_yok:
        cv2.namedWindow(pencere, cv2.WINDOW_NORMAL)

    # Fare ile tiklanan dugmeler: {"kapat": (x1, y1, x2, y2), ...}
    butonlar = {}
    tiklanan = []

    def fare(olay, x, y, *_):
        if olay == cv2.EVENT_LBUTTONDOWN:
            for ad, (x1, y1, x2, y2) in butonlar.items():
                if x1 <= x <= x2 and y1 <= y <= y2:
                    tiklanan.append(ad)

    if not args.pencere_yok:
        cv2.setMouseCallback(pencere, fare)

    if algilayici.kalibrasyon_gerekli:
        kal = Kalibrasyon(alarm)
        esikler = None
    else:
        kal = None
        esikler = {"ear": 0.15, "kirpma": None, "egim": None}

    kapali_bas = None      # gozlerin kapandigi an
    acik_gecici = None     # gozler kapaliyken anlik acilma (tolerans icin)
    yuz_yok_bas = None     # yuzun kayboldugu / basin dustugu an
    yuz_gecici = None      # yuz yokken anlik gorunme (tolerans icin)
    acik_bas = None        # alarm calarken gozlerin acildigi an
    ear_gecmis = []        # titremeyi azaltmak icin kisa ortalama
    susturma_bitis = 0

    print(f"Algilama yontemi: {algilayici.ad}")
    print(
        f"Gozler {args.sure:.0f} sn kapali kalirsa alarm calacak."
        + (f" Yuz {args.yuz_yok:.0f} sn gorunmezse / bas duserse de." if args.yuz_yok > 0 else "")
    )

    try:
        while True:
            ok, kare = kamera.read()
            if not ok:
                time.sleep(0.05)
                continue
            kare = cv2.flip(kare, 1)
            simdi = time.time()
            olcum = algilayici.olc(kare)

            if olcum["ear"] is not None:
                ear_gecmis.append(olcum["ear"])
                ear_gecmis = ear_gecmis[-3:]
                olcum["ear"] = sum(ear_gecmis) / len(ear_gecmis)

            # ---- Kalibrasyon ----
            if kal is not None and kal.aktif:
                kal.guncelle(olcum, simdi)
                if not kal.aktif:  # basariyla bitti
                    esikler = kal.esikler
                    kapali_bas = yuz_yok_bas = acik_bas = acik_gecici = yuz_gecici = None

            izleniyor = esikler is not None and not (kal is not None and kal.aktif)
            gozler_kapali = False
            bas_dustu = False
            sebep = ""

            if izleniyor:
                # ---- Bas dustu mu? ----
                if (
                    args.bas_acisi > 0
                    and olcum["yuz"]
                    and olcum["egim"] is not None
                    and esikler.get("egim") is not None
                ):
                    fark = abs((olcum["egim"] - esikler["egim"] + 180) % 360 - 180)
                    bas_dustu = fark > args.bas_acisi
                yuz_gorunuyor = olcum["yuz"] and not bas_dustu

                # ---- Yuz yok sayaci (anlik gorunmeler sayaci sifirlamaz) ----
                if yuz_gorunuyor:
                    if yuz_yok_bas is not None:
                        yuz_gecici = yuz_gecici or simdi
                        if simdi - yuz_gecici >= YUZ_VAR_TOLERANS:
                            yuz_yok_bas = yuz_gecici = None
                else:
                    yuz_gecici = None
                    yuz_yok_bas = yuz_yok_bas or simdi

                # ---- Goz kapali sayaci (anlik acilmalar sayaci sifirlamaz) ----
                if yuz_gorunuyor:
                    gozler_kapali = gozler_kapali_mi(olcum, esikler, args.esik)
                    if gozler_kapali:
                        acik_gecici = None
                        kapali_bas = kapali_bas or simdi
                    elif kapali_bas is not None:
                        acik_gecici = acik_gecici or simdi
                        if simdi - acik_gecici >= GOZ_ACIK_TOLERANS:
                            kapali_bas = acik_gecici = None

                uyku = False
                if kapali_bas is not None and simdi - kapali_bas >= args.sure:
                    uyku, sebep = True, "GOZLER KAPALI"
                if args.yuz_yok > 0 and yuz_yok_bas is not None and simdi - yuz_yok_bas >= args.yuz_yok:
                    uyku, sebep = True, ("BAS DUSTU" if bas_dustu else "YUZ GORUNMUYOR")

                if uyku and simdi >= susturma_bitis:
                    if not alarm.aktif:
                        print(time.strftime("[%H:%M:%S]"), "UYAN! ->", sebep)
                        if not args.pencere_yok:
                            try:
                                cv2.setWindowProperty(pencere, cv2.WND_PROP_TOPMOST, 1)
                            except Exception:
                                pass
                    alarm.baslat()

                # Alarm caliyorsa: yuz gorunur ve gozler uyanik-sure boyunca acik kalinca sustur
                if alarm.aktif:
                    if yuz_gorunuyor and not gozler_kapali:
                        acik_bas = acik_bas or simdi
                        if simdi - acik_bas >= args.uyanik_sure:
                            alarm.durdur()
                            acik_bas = kapali_bas = yuz_yok_bas = acik_gecici = yuz_gecici = None
                            print(time.strftime("[%H:%M:%S]"), "Uyandin, alarm sustu.")
                    else:
                        acik_bas = None

            # ---- Ekran ----
            if not args.pencere_yok:
                for px, py in olcum["noktalar"]:
                    cv2.circle(kare, (int(px), int(py)), 2, (0, 255, 0), -1)

                if kal is not None and kal.aktif:
                    kal.ciz(kare, simdi)
                else:
                    if alarm.aktif and int(simdi * 4) % 2 == 0:
                        kirmizi = np.zeros_like(kare)
                        kirmizi[:] = (0, 0, 255)
                        kare = cv2.addWeighted(kare, 0.4, kirmizi, 0.6, 0)
                    if alarm.aktif:
                        orta_yazi(kare, "UYAN!", kare.shape[0] // 2, (255, 255, 255), 2.5, 6)
                        yazi(kare, "Gozlerini acik tut ya da SUSTUR'a bas", (10, kare.shape[0] - 20), (255, 255, 255), 0.6)

                    if yuz_yok_bas is not None:
                        ne = "Bas dustu" if bas_dustu else "Yuz bulunamadi"
                        if args.yuz_yok > 0:
                            durum = f"{ne}: {simdi - yuz_yok_bas:.1f} / {args.yuz_yok:.0f} sn"
                        else:
                            durum = ne
                        renk = (0, 165, 255)
                    elif kapali_bas is not None:
                        durum, renk = f"Gozler kapali: {simdi - kapali_bas:.1f} / {args.sure:.0f} sn", (0, 0, 255)
                    else:
                        durum, renk = "Gozler acik - izleniyor", (0, 255, 0)
                    yazi(kare, durum, (10, 30), renk)
                    if olcum["ear"] is not None and esikler:
                        bilgi = f"Goz acikligi: {olcum['ear']:.3f} (esik {args.esik or esikler['ear']:.3f})"
                        if olcum["kirpma"] is not None and esikler.get("kirpma"):
                            bilgi += f"  kirpma: {olcum['kirpma']:.2f} (esik {esikler['kirpma']:.2f})"
                        yazi(kare, bilgi, (10, 58), (255, 255, 255), 0.5, 1)
                    yazi(kare, "q: cikis  bosluk: sustur  c: kalibre", (10, 82), (200, 200, 200), 0.5, 1)

                butonlar.clear()
                w = kare.shape[1]
                butonlar["kapat"] = buton_ciz(kare, "KAPAT", w - 130, 10, w - 10, 55, (50, 50, 50))
                if alarm.aktif:
                    h = kare.shape[0]
                    butonlar["sustur"] = buton_ciz(kare, "SUSTUR", w // 2 - 90, h - 110, w // 2 + 90, h - 55, (0, 140, 0))

                cv2.imshow(pencere, kare)
                tus = cv2.waitKey(1) & 0xFF
                tikla = set(tiklanan)
                tiklanan.clear()
                if tus in (ord("q"), 27) or "kapat" in tikla:
                    break
                if tus == ord(" ") or "sustur" in tikla:
                    alarm.durdur()
                    kapali_bas = yuz_yok_bas = acik_gecici = yuz_gecici = None
                    susturma_bitis = simdi + 5  # 5 sn yeniden calma
                if tus == ord("c") and kal is not None:
                    alarm.durdur()
                    kal.basla()
                try:
                    if cv2.getWindowProperty(pencere, cv2.WND_PROP_VISIBLE) < 1:
                        break
                except Exception:
                    pass
    except KeyboardInterrupt:
        pass
    finally:
        alarm.durdur()
        kamera.release()
        algilayici.kapat()
        try:
            cv2.destroyAllWindows()
        except cv2.error:
            pass
        print("Cikildi.")


if __name__ == "__main__":
    main()
