# Trạng thái bản đầu tiên — 13/11/2026

## Phạm vi đã có source

- FastAPI + giao diện tiếng Việt, chạy ở `127.0.0.1:8765`; toàn bộ CSS/JS local.
- Upload audio, cắt clip bằng FFmpeg, một tác vụ AI tại một thời điểm, lưu job trên đĩa.
- ADTOF và ADT_STR chạy local từ revision/checkpoint đã pin; thiếu weights phải báo lỗi.
- Chuẩn hóa nhạc cụ ADT_STR trước khi xuất General MIDI và sheet.
- Giữ sự kiện gốc; căn nhịp riêng bằng BPM/meter/grid/downbeat do người dùng xác nhận.
- MusicXML, MIDI gốc, MIDI theo sheet, JSON và WAV tổng hợp để nghe đối chiếu.
- Hai voice, percussion clef, tempo, số chỉ nhịp, beam/rest/tuplet; ký hiệu biểu cảm khi có marker tường minh.
- Xuất lại sheet từ kết quả đã lưu, không chạy AI lại và không thay dữ liệu gốc.
- Tích hợp tùy chọn Demucs local và xuất PDF bằng MuseScore đã cài trên máy.

## Những gì đã kiểm tra

| Phần | Kết quả |
|---|---|
| Tests | 115 tests pass: 9 engine, 101 notation, 5 API/job |
| Lint / cú pháp | Ruff, Python compile, JavaScript syntax và HTML/DOM reference checks pass |
| ADTOF thật | Checkpoint load strict; suy luận CPU trên 8 giây audio tổng hợp; không gọi mạng |
| ADT_STR thật | Checkpoint load strict; toàn bộ adapter + subprocess chạy CPU trên 2.56 giây audio tổng hợp; không gọi mạng |
| Luồng API + model thật | Upload WAV → FFmpeg → ADTOF → events/MIDI/MusicXML/preview thành công, chặn socket trong lúc chạy |
| Xuất lại | Tạo job mới dùng nguyên sự kiện gốc; tempo/grid thay đổi chỉ ảnh hưởng bản ký âm |
| Cấu trúc nhạc | Mọi meter/grid được hỗ trợ có voice đủ trường độ; kiểm tra ánh xạ MIDI, trường độ, tuplet, marker trong chord |
| Hình thức sheet | MusicXML demo đã render và xem bằng Verovio: tempo, khuông percussion, stem/beam/rest và các marker hiển thị |

Môi trường kiểm tra là Linux, Python 3.12, PyTorch/TorchAudio 2.8 CPU.
Chi tiết inference và checksum ở [MODELS.md](MODELS.md).

## Những phần còn phải xác nhận trên PC

- **Chất lượng nhận diện trên nhạc thật**: chưa có clip có nhãn của người dùng; các audio tổng hợp chỉ dùng kiểm tra thực thi, không đo accuracy.
- **PDF bằng MuseScore**: adapter đã có, nhưng MuseScore AppImage không khởi động được trong container không có display. Kiểm tra hình thức bằng Verovio không thay cho xác nhận PDF MuseScore.
- **Giao diện trong trình duyệt**: đã kiểm tra cú pháp/contract với backend; chưa xác nhận screenshot browser vì Chromium không khởi động được trong môi trường này.
- **Demucs thực tế**: đường gọi với local model repo đã triển khai; chưa chạy inference tách stem trong lượt kiểm tra này.
- **Windows/WSL, CUDA/MPS và hiệu năng cả bài**: cần kiểm tra trên cấu hình thực của bạn.

App chưa có hủy tác vụ đang chạy; dừng server có thể cần đợi inference kết thúc. Giới hạn upload 200 MB được kiểm tra sau khi multipart parser đã nhận/spool file. Đây là bản local một người dùng, chưa có cơ chế public hosting.

## Bước tiếp theo hữu ích nhất

Chạy cùng ba clip 30–60 giây qua ADTOF và ADT_STR, mở MusicXML trong MuseScore, ghi lỗi thiếu/thừa/nhầm hit và số phút chỉnh tay theo [VALIDATION.md](VALIDATION.md). Kết quả đó quyết định có cần ưu tiên tách nguồn, cải thiện tempo, đổi model hay fine-tune trước khi mở rộng giao diện.
