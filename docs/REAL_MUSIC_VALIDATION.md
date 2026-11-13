# Kiểm tra bản nhạc đã phát hành có chuyển nhịp

Ngày kiểm tra: **14/11/2026, Asia/Saigon**. Source app: [`96fd763`](https://github.com/sonvhse06072/Drum-transcription/commit/96fd763d18ae1c58a5e204d70dd87800b0b727f8), v0.3.

## Bản đã tải và chạy: Room 34 — Micro-Prog

Tác phẩm mang tên đầy đủ *The Fall of the Village of the Infinitesimal Forest at the Hands of the Royal Aggressor [An Epic in Seven Parts]*, của **Scott Anderson / Room 34**. Đây là bản nhạc đã phát hành, có guitar, bass và recorder thu thật cùng trống MIDI và keyboard. Không phải fixture được tạo riêng cho app, nhưng cũng không phải bản thu drum kit acoustic.

- [Bài viết của chính tác giả](https://blog.room34.com/archives/5278): công bố CC0, nhạc cụ sử dụng và số chỉ nhịp của từng ô.
- [Trang audio trên Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Room_34_-_24_-_The_Fall_of_the_Village_of_the_Infinitesimal_Forest_at_the_Hands_of_the_Royal_Aggressor_An_Epic_in_Seven_Parts.ogg).
- [Nghe/tải bản OGG](https://upload.wikimedia.org/wikipedia/commons/b/b6/Room_34_-_24_-_The_Fall_of_the_Village_of_the_Infinitesimal_Forest_at_the_Hands_of_the_Royal_Aggressor_An_Epic_in_Seven_Parts.ogg).
- [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/). Commons chưa hoàn tất rà soát giấy phép của bản mirror; bài viết gốc của tác giả xác nhận CC0 độc lập. Link MP3 cũ trong bài viết hiện chuyển tới ảnh thông báo xóa, nên phép thử dùng bản OGG lưu trên Commons.

Audio tải được có **14.576326530612246 giây**, 44,100 Hz, stereo, 328,639 bytes. SHA-1 khớp metadata của Commons. SHA-256 dùng xác định chính xác input:

```text
93b965383f397943d7526c7a150f3beff80ca42b544432659889a99f4defa4eb
```

Tác giả mô tả **bảy ô nhịp** theo thứ tự:

| Ô | Số chỉ nhịp |
|---:|---|
| 1 | 6/8 |
| 2 | 7/8 |
| 3 | 2/4 |
| 4 | 2/4 |
| 5 | 7/8 |
| 6 | 9/8 |
| 7 | 5/16 |

Danh sách này có năm lần thay đổi số chỉ nhịp vì hai ô 2/4 liền nhau giữ nguyên meter. Tác giả không cung cấp BPM hoặc timestamp từng barline; không tự gán các giá trị đó làm annotation chuẩn.

## Kết quả thực tế

Chạy toàn bộ bản OGG, không truyền BPM/meter hint, CPU với hai Torch threads. Python socket/DNS bị chặn trong lúc suy luận; có **0 lần gọi mạng**. Dùng adapter local và exporter trực tiếp, không coi đây là kiểm tra HTTP hoặc giao diện trình duyệt.

| Hạng mục | Quan sát |
|---|---|
| Beat This! | Hoàn tất trong 2.032 giây; tạo 18 ô, gồm một ô cuối ngoại suy |
| Chuỗi meter | Không khôi phục được bảy ô do tác giả công bố; nhận chủ yếu 2/4, 4/4 và một số 1/4, 3/4 |
| BPM / phách đầu | 187.5 nốt đen/phút và 0.06 giây; chưa chấm sai số vì thiếu annotation chuẩn |
| Trạng thái kiểm tra nhịp | `unstable`: 10 lần đổi cách đếm và bốn khoảng downbeat chỉ có một beat |
| ADTOF + xuất sheet | Hoàn tất trong 2.408 giây; 78 sự kiện nhận diện và 78 nốt trên sheet, 18 ô |
| File xuất | MusicXML, MIDI gốc, MIDI theo sheet có tempo map, audio trống tổng hợp và audio click |

**Kết luận của ca thử: nhận diện meter thất bại so với chuỗi tác giả.** Xuất được file và giữ đủ 78 sự kiện chỉ xác nhận luồng thực thi/tính toàn vẹn dữ liệu. Chưa có annotation cú đánh để chấm precision/recall của ADTOF; chưa có timestamp từng ô để chấm độ lệch downbeat.

[Kết quả máy đọc được](validation/room34-micro-prog.json) lưu nguồn, hash, model revision/checksum, chuỗi nhịp kỳ vọng và toàn bộ bản đồ nhận diện. Audio/weights/file xuất không được commit vào repo.

## Tái kiểm tra trên PC

Tải bản OGG từ nguồn trên, mở app, chọn ADTOF và nhận diện nhịp tự động, để trống gợi ý meter, chọn từ giây 0 với độ dài 15 giây. So sánh bản đồ nhận diện với chuỗi bảy ô của tác giả. Không chỉnh trước meter/BPM để biến kết quả suy đoán thành kết quả đúng theo dữ liệu đã nhập.

Khi thay bộ phát hiện hoặc bộ giải mã nhịp, dùng cùng input hash và lưu một kết quả mới. Mục tiêu đầu tiên là khôi phục đúng cấu trúc ô; đánh giá timing chính xác cần thêm annotation độc lập.

## Bài dài hơn đã tìm được: The Boy Loves — Quarkstar

[Trang tác giả trên ccMixter](https://ccmixter.org/files/Quarkstar/56030) ghi bản dài 4:28, 120 BPM, có trống/bass/synth/giọng hát SackJo22 và chuyển **4/4 → 6/4 ở đoạn break giữa bài**, theo giấy phép **CC BY-NC-SA 3.0**. Tác giả chưa ghi timestamp chuyển cụ thể.

Player/API công khai cung cấp [MP3](https://ccmixter.org/content/Quarkstar/Quarkstar_-_The_Boy_Loves.mp3), nhưng yêu cầu tải trong môi trường kiểm tra nhận HTTP 403. Không tìm được endpoint chính thức khác giải quyết lỗi; chưa có audio local và **chưa chạy app trên bài này**. Trang bài hát vẫn dùng được làm liên kết nghe và nguồn xác nhận có đổi nhịp. Chưa suy ra mốc chuyển từ dự đoán của model hoặc từ vị trí giữa file.
