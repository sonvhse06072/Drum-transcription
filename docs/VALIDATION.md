# Kịch bản kiểm chứng chất lượng

[Ca nhạc đã phát hành: Room 34 / Micro-Prog](REAL_MUSIC_VALIDATION.md) đã có nguồn audio CC0, chuỗi bảy ô do tác giả mô tả và kết quả model thật. Đây là một ca nhận diện meter thất bại được giữ để tái kiểm tra, không phải ca accuracy đạt.

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

### Đổi nhịp và nhịp lẻ

Thử riêng những ca sau, có ít nhất vài ô ở mỗi phần nếu muốn kiểm tra suy luận tự động:

| Ca | Điều cần xác nhận |
|---|---|
| 4/4 → 3/4 → 4/4 | Ranh giới ô trùng phách 1, nốt hai phía ranh giới không mất/nhân đôi |
| Một ô 5/8 giữa các ô 4/4 | Không ép ô ngắn thành 4/4; quan sát nếu model bỏ sót downbeat |
| 7/8 nhóm 2+2+3 và 3+2+2 | Số chỉ nhịp và cách nối móc là hai lựa chọn riêng; nghe nhóm dài/ngắn |
| 5/4 so với 5/8 | Cách hiểu pulse/mẫu số, quarter BPM và số ô đúng với bản tham chiếu |
| 11/8, 13/16 | Dùng để đánh giá giới hạn detector, không chỉ khả năng xuất XML |
| 120 → 90 BPM, vẫn 4/4 | Tempo thay đổi, số chỉ nhịp giữ nguyên; score MIDI theo audio sau quantization |
| 2/4 ↔ 4/4 | Phân biệt đổi nhịp thật với half/double-time hoặc thêm/bỏ downbeat |

Lưu cả bản trước và sau khi chỉnh từng ô. Ghi chính xác phần nào model tự suy ra đúng, phần nào do người dùng sửa. `rhythm.json.bars` là bản đồ đang sử dụng; `events.json.rhythm` giữ nhận diện ban đầu. Click mạnh hiện đánh dấu downbeat model trả về, không xác nhận mọi ranh giới được nội suy. Một lần xuất thành công từ các marker có sẵn chỉ xác nhận tầng ký âm, không chứng minh khả năng nghe nhịp của model.

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

## 6. Tái lập chẩn đoán nhịp bằng fixture tổng hợp

Chạy từ thư mục repo sau khi cài dependency và checkpoint Beat This!:

```bash
python scripts/check_meter.py --models-dir models --output-dir data/meter-check --fixture all --device cpu --threads 2
```

Script tạo ba WAV với seed `2704`: `mixed` (8 ô 4/4 → 8 ô 3/4 → 8 ô 7/8), `seven` (12 ô 7/8), `five` (16 ô 5/8). Tất cả có quarter BPM chuẩn 120 và phách 1 đầu tiên tại 0,371 giây. Có thể chọn riêng `--fixture seven`; audio vẫn giống khi chọn `all`. Dùng `--generate-only` để chỉ tạo audio và annotation, không import/chạy AI. `--device` cũng nhận `auto`, `cuda`, `mps`; thiếu model hoặc thiết bị sẽ ghi lỗi thật và trả exit code khác 0, không tải model hoặc ép kết quả về nhịp kỳ vọng.

`report.json` chứa annotation, kết quả, số beat giữa các downbeat, metadata, cảnh báo/lỗi và số lần gọi mạng bị chặn; mỗi kết quả thành công còn có `<fixture>-analysis.json`. Trường annotation `eighth_groups` luôn tính bằng nốt móc đơn, nên 4/4 là `[2,2,2,2]`; nó khác `grouping` của bar map, vốn tính theo mẫu số của số chỉ nhịp. Script chặn Python socket `connect`, `create_connection`, `getaddrinfo` trong lúc gọi model; `network_attempts: 0` chỉ có ý nghĩa cùng trạng thái inference thành công. Đây không phải cơ chế cô lập mạng ở cấp hệ điều hành. Report ghi rõ `fixture_synthetic_not_accuracy_benchmark`: kết quả giúp tái lập lỗi detector trên tín hiệu đã biết, không thay việc chấm chất lượng trên bản nhạc thật.

## Independent meter-head validation (optional)

An additional adapter can use [SheetSage2-Refined](SHEETSAGE2.md) to predict bar meters directly. Use **the same audio cut, original event timing and drum engine** when comparing with Beat This!. Do not pass the composer meter sequence, bar boundaries or any annotated audio information into either model. Record separate bar counts and exact meter sequences for both engines, and distinguish an automated estimate from the user-editable bar map.

The 7-bar Room 34 composer sequence remains the regression target. A CI or unit-test PASS on adapter infrastructure is **not** evidence that the model recognizes this recording correctly.
