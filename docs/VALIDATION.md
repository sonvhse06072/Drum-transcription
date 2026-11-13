# Kịch bản kiểm chứng chất lượng

## 1. Tách hai câu hỏi

**Phần mềm chạy được**: model thật tải local, audio được xử lý, sự kiện hợp lệ, MusicXML/MIDI mở được, app không gọi mạng khi suy luận.

**Sheet có đủ tốt cho bạn**: cần nghe và đối chiếu với bản nhạc thật, có bản tham chiếu đã kiểm tra. Test phần mềm hoặc demo ký âm không trả lời thay câu hỏi này.

## 2. Bộ thử nhỏ trước tiên

Chọn ba đoạn 30–60 giây mà bạn có quyền sử dụng:

| Clip | Nội dung | Cần nghe kỹ |
|---|---|---|
| Groove rõ | Ít nhạc cụ che trống, tempo đều | Kick/snare/hat thiếu hoặc thừa, phách đầu |
| Fill | Nhiều tom/cymbal, chuyển đoạn | Nhầm tom, crash/ride, lỗi ranh giới đoạn |
| Chi tiết khó | Ghost, hi-hat mở, nhiều cú đánh cùng lúc | Chi tiết model hỗ trợ và chi tiết bị gộp hoặc mất |

Với mỗi clip, thử **nhận diện nhịp tự động** trước và nghe bản có click để kiểm tra tempo/phách 1. Khi so sánh engine nhận diện trống, dùng cùng kết quả nhịp hoặc cùng cấu hình ghi đè thủ công để tránh trộn hai loại sai số. Thử audio mix trực tiếp và drum stem nếu có. Tách nguồn có thể làm mất transient/ghost note; không mặc định stem luôn tốt hơn.

Thêm các ca lệch đầu đoạn: im lặng 1,5 giây trước groove, cắt ở mốc 1,5 giây trong file, và clip bắt đầu giữa ô nhịp có vài cú đánh trước phách 1. Kiểm tra mốc clip và mốc file tải lên, giữ số giây lẻ, đồng thời đảm bảo phần lấy đà vẫn có trên sheet. Beat This! không đồng nghĩa cú đánh đầu tiên là phách 1.

ADTOF chỉ có 5 nhóm: không chấm phân biệt tom/hi-hat/crash–ride như thể model hỗ trợ chúng. ADT_STR có nhiều nhãn hơn nhưng vẫn cần nghe/chấm lại; số lớp không phải accuracy.

## 3. Ghi kết quả mỗi lần

| Trường | Giá trị cần ghi |
|---|---|
| Clip / job ID | Tên, vị trí cắt, độ dài |
| Model | Engine, revision/checkpoint, threshold/device |
| Nhịp | Auto/manual, BPM, meter, grid, first downbeat trong clip và file tải lên |
| Lỗi hit | Thiếu, thừa, nhầm loại theo từng nhạc cụ |
| Lỗi nhịp | Onset sai, ô nhịp sai, chùm ba/swing sai |
| Chi tiết | Kỹ thuật đúng, sai, model không hỗ trợ |
| Sheet | Beam/rest/voice, vị trí nốt, số ô trên dòng, khả năng đọc khi chơi |
| Hiệu quả | Phút sửa tay, phút tự chép, thời gian chạy, RAM/VRAM |

Nếu có annotation chuẩn, dùng onset tolerance đã chọn trước (ví dụ ±50 ms) và matching **một–một theo từng lớp** để tính precision/recall/F1. Báo rõ tolerance và các lớp gộp. Không gán confidence của model thành xác suất chính xác thực tế; chưa có bước calibration.

## 4. Kiểm tra offline

Sau khi cài dependency/model:

1. Chạy `drum-transcription doctor` để xác nhận đường dẫn và file cần thiết.
2. Tắt kết nối mạng.
3. Khởi động app, nạp một clip mới, chạy model, nghe kết quả và xuất MusicXML/PDF.
4. Khởi động lại app, mở lại job cũ và xuất lại ở tempo khác.

Không dùng kết quả demo để thay bước nhận diện audio thật. Thiếu checkpoint phải báo lỗi; tuyệt đối không chấp nhận model random hoặc kết quả giả.

## 5. Ngưỡng quyết định

Chốt sau ba clip: thời gian sửa có giảm đủ đáng kể không; loại lỗi có khắc phục được bằng ngưỡng/beat/grid hay do model thiếu lớp; chất lượng có nhất quán trên nhiều clip không. Nếu chưa tốt, ưu tiên thay/tinh chỉnh model hoặc tách nguồn trước khi thêm chức năng giao diện.

Đánh giá cả bài chỉ thực hiện sau khi các clip ngắn cho kết quả chấp nhận được. Đừng suy ra độ chính xác cả bài từ một groove đơn giản.
