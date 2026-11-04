# UYAN 😴➜⏰

Bilgisayarının kamerasıyla seni izler. **Gözlerin birkaç saniyeden uzun kapalı kalırsa ya da yüzün / başın 10 saniye kameradan kaybolursa veya öne düşerse** (yani uyuyakalırsan) **kulaklığından / hoparlöründen gittikçe yükselen bir alarm** çalar. Gözlerini 2 saniye açık tuttuğunda alarm kendiliğinden susar.

## Nasıl çalışır?

1. **Kamera:** Web kameran her kareyi okur (görüntü hiçbir yere gönderilmez, her şey bilgisayarında işlenir).
2. **Göz takibi:** Google MediaPipe yüz modeli yüzünde 478 nokta bulur. İki ölçüm birlikte kullanılır: göz kapakları arasındaki mesafe (*göz açıklığı*, EAR) ve modelin kendi *göz kırpma skoru*. Kırpma skoru gözlük parlamasından çok daha az etkilendiği için **gözlükle de** yanlış alarm vermez. Tek karelik hatalar (parlama, anlık titreme) sayaçları sıfırlamaz.
3. **Kalibrasyon:** Program açılınca "KALİBRASYON BAŞLIYOR" yazar ve kısa bir ses çıkar. Ardından ekranda **3-2-1 geri sayım** olur: bu sürede gözlüğün takılıyken, gözlerin açık düz kameraya bak. Sonunda **BAŞARILI** (yükselen ses) ya da **BAŞARISIZ + sebebi** (kalın ses) yazar; başarısızsa kendiliğinden tekrar dener. Kalibrasyon senin gözüne ve normal baş duruşuna göre eşikleri ayarlar.
4. **Alarm:** Gözler `--sure` saniye (varsayılan 3) kapalı kalırsa **veya yüzün `--yuz-yok` saniye (varsayılan 10) görünmezse ya da başın kalibrasyondaki duruşa göre 30°'den fazla öne/arkaya düşerse** alarm çalar. Ses, Windows/Mac'te **varsayılan ses çıkışından** çıkar, yani kulaklık takılıysa kulaklıktan. Ses %30'dan başlayıp ~10 saniyede en yükseğe çıkar, ekran kırmızı yanıp söner ve pencere öne gelir.
5. **Susma:** Gözlerini 2 saniye açık tut → alarm susar. Ya da ekrandaki **SUSTUR** düğmesine tıkla / **BOŞLUK** tuşuna bas. Programı kapatmak için sağ üstteki **KAPAT** düğmesine tıkla.

## Kurulum

### Seçenek A – Hazır .exe (Windows, Python gerekmez)
GitHub'da bu deponun **Actions** sekmesine gir → en son "Windows exe olustur" çalışmasına tıkla → en alttaki **UYAN-windows** dosyasını indir, zip'ten çıkar, `UYAN.exe`'ye çift tıkla.

> Windows "bilinmeyen yayıncı" uyarısı verirse **Ek bilgi → Yine de çalıştır** de.

### Seçenek B – Python ile (Windows / Mac / Linux)
1. [Python 3.10–3.12](https://www.python.org/downloads/) kur (Windows'ta kurulumda **"Add Python to PATH"** kutusunu işaretle).
2. Bu klasörde:
   - **Windows:** `baslat.bat` dosyasına çift tıkla.
   - **Mac / Linux:** terminalde `./baslat.sh`
   
   İlk açılışta gerekli paketler otomatik kurulur (birkaç dakika sürebilir).

Kendin exe üretmek istersen Windows'ta `exe_yap.bat`'a çift tıkla → `dist\UYAN.exe`.

## Ayarlar

```bash
python uyan.py --sure 5            # 5 saniye göz kapalı kalınca çal
python uyan.py --yuz-yok 20        # yüz 20 sn görünmezse çal (varsayılan 10)
python uyan.py --yuz-yok 0         # yüz kaybolunca alarmı kapat
python uyan.py --bas-acisi 40      # baş 40° düşünce 'baş düştü' say (varsayılan 30, 0 = kapalı)
python uyan.py --ses alarm.mp3     # kendi alarm sesini (mp3/wav/ogg) kullan
python uyan.py --ses-seviyesi 0.6  # en yüksek ses seviyesi (0-1)
python uyan.py --uyanik-sure 3     # alarmın susması için 3 sn gözler açık
python uyan.py --kamera 1          # ikinci kamerayı kullan
python uyan.py --haar              # MediaPipe çalışmazsa basit yedek yöntem
```
Exe için de aynı: `UYAN.exe --sure 5 --yuz-yok 15`

| Tuş | İşlev |
|---|---|
| **KAPAT** düğmesi (sağ üst) / `q` / `ESC` | Çıkış |
| **SUSTUR** düğmesi (alarm çalarken) | Alarmı sustur (5 sn tekrar çalmaz) |
| `BOŞLUK` | Alarmı sustur (5 sn tekrar çalmaz) |
| `c` | Yeniden kalibre et |

## İpuçları
- Yüzüne iyi ışık gelsin; karanlıkta kamera gözlerini göremez.
- Kamera yüzünü önden görsün; çok aşağıdan/yandan açılar hassasiyeti düşürür.
- **Gözlük:** Kalibrasyonu mutlaka gözlüğün takılıyken yap. Parlamayı azaltmak için ekran parlaklığını biraz düşür ya da ışığı yandan değil önden al. Gözlüğü taktıysan/çıkardıysan `c` ile yeniden kalibre et.
- Yanlış alarm çok oluyorsa `--sure` değerini artır; geç algılıyorsa `--esik 0.22` gibi daha yüksek bir eşik dene.
- Bilgisayarın uyku moduna geçmesin (Güç ayarlarından "Ekranı kapatma / Uyku: Hiçbir zaman").
- Masadan kalkacaksan programı kapat; yüzün görünmezse 10 sn sonra alarm çalar.
- Program internete bağlanmaz ve Claude kullanmaz (sadece ilk açılışta, exe paketinde değilse, yüz modelini bir kez indirir).
- Ses seviyesini sistemde de açık tut; program sistem sesini aşamaz.

> ⚠️ Bu program sadece bir yardımcıdır; araç kullanırken ya da güvenliğin uyanık kalmana bağlıyken buna güvenme.
