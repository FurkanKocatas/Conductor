# Conductor Koordinasyon Protokolü (CLAUDE.md'ye ekle)

Bu projede birden çok Claude ajanı ayrı makinelerde çalışıyor. Çakışmayı önlemek ve
işbölümü yapmak için **Conductor** MCP sunucusunu kullan. Kimliğin token'ından gelir
(sen kimsin: `whoami`). Kurallar:

## Oturum başında
1. `register(machine="<makinen>")` — kendini panoya kaydet.
2. `sync()` — ekip ne yapıyor, senin açık görevlerin, okunmamış mesajların? Duruma göre hareket et.

## İş alma döngüsü
3. `claim_next_task()` ile sıradaki uygun görevi al (atomik — başka ajan aynı görevi almaz).
   - Görev döndüyse: `heartbeat(status="working", note="<kısa ne yapıyorsun>")`.
   - `{claimed:null}` ise: `sync()` ile panoya bak; gerekli işi `create_task(...)` ile ekle.
4. Bir dosyayı düzenlemeden önce **kilitle**: `acquire_file_lock("file:<yol>")`.
   Alamazsan başka ajan üstünde demektir — başka göreve geç veya `post_message` ile konuş.
5. İş bitince: `update_task(task_id, status="done", artifacts={"commit":"...","files":[...]})`
   ve `release_file_lock("file:<yol>")`.
   - Bloke olduysan: `update_task(task_id, status="blocked")` + `post_message` ile neyin
     beklediğini yaz.
   - İncelemeye gidecekse: `status="review"`.

## İletişim
- Ekibe bir şey söylemek/sorman gerekirse `post_message(body, to_agent="<isim veya boş>")`.
- Ara ara `read_messages()` ile sana geleni oku; `sync()` ile panoyu tazele.

## İş üretme (planlama)
- Büyük işi parçalara böl: her parça için `create_task(title, spec, priority,
  depends_on=[...], assign_mode="auto")`. Sıra/bağımlılık Conductor'da; sen sadece tanımla.
- Belirli birine iş vermek istersen `assign_mode="manual", assignee="<isim>"`.

## Altın kural
Aynı anda aynı dosyaya iki ajan dokunmasın. Şüphedeysen **önce kilitle veya mesajlaş**.
