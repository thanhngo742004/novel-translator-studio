# Deep Research Phase 3 cho Novel Translator Studio

## Phạm vi, nguyên tắc và quyết định kiến trúc chính

**PHASE3_MANGA_RESEARCH_REPORT.md**

Phase 3 nên được hiểu là một thiết kế chuyên sâu cho **module manga/comic/manhwa/manhua** nằm **bên trong app chính** của Novel Translator Studio, không phải một app riêng, không phải một plugin riêng, và cũng không phải một lần “thiết kế lại toàn hệ thống”. Điều này bám đúng hai nền đã có từ các phase trước: Phase 1 xác định LAMM-T là một hệ memory **structured-first**, có **scope, confidence, evidence, provenance**, và plugin chỉ nhận **compact memory export** ở dạng read-only, không tự học; Phase 2 xác định kiến trúc tổng thể là **Python-first, CLI-first, SQLite local-first**, với GUI mỏng nằm trên service layer. fileciteturn15file0L30-L40 fileciteturn15file0L213-L217 fileciteturn15file1L24-L27 fileciteturn15file1L31-L32 fileciteturn15file1L81-L87

Kết luận điều hành của Phase 3 là: **không nên bắt đầu bằng full-auto**, mà nên bắt đầu bằng **semi-manual pipeline có box ID ổn định, OCR có thể sửa tay, dịch theo manifest có kiểm tra ID, cleaning mức đơn giản trước, typesetting local trước, rồi mới dần tăng automation bằng detector/OCR/inpainting/speaker hints**. Hướng đi này phù hợp với trạng thái thực tế của các dự án mã nguồn mở hiện nay: nhiều tool mạnh ở mức demo hoặc workflow tích hợp, nhưng chính các repo lớn vẫn thừa nhận hạn chế ở text rendering, English/Korean detection, hoặc cần manual correction / manual mode để sửa lỗi OCR, detection, cleaning. citeturn18view2turn19view6turn19view7turn25view5

Nếu phải chốt một quyết định kỹ thuật duy nhất ngay từ đầu, thì quyết định đó là: **canonical object của module manga không phải “bức ảnh đã dịch”, mà là `page + stable box IDs + manifest + audit trail`**. Ảnh cleaned, ảnh typeset, CBZ, PDF, report QA, mask, crop OCR, preview… đều là artifact sinh ra từ manifest và các version của box. Thiết kế này vừa khớp Phase 2 service-layer/local-first, vừa cho phép LAMM-T học từ correction episode có bằng chứng cụ thể thay vì “nuốt cả ảnh như black box”. fileciteturn15file0L30-L40 fileciteturn15file1L81-L87

Về ưu tiên công cụ, **OCR mặc định nên là `manga-ocr` cho Nhật** và **PaddleOCR cho các ngôn ngữ khác hoặc fallback đa ngữ**; **detection tự động ở MVP không nên là hard dependency**, mà nên đi sau manual/imported boxes; **cleaning nên khởi đầu bằng white/color fill + OpenCV inpaint cục bộ**, còn **LaMa** và các wrapper như **IOPaint** nên để sang bước nâng cấp; **typesetting MVP nên dùng Pillow/OpenCV**, không chờ engine dàn trang “xịn như Photoshop”. `manga-ocr` được thiết kế riêng cho manga Nhật, hỗ trợ vertical/horizontal, furigana, text trên nền ảnh, nhiều font, và OCR nhiều dòng trong một bubble; PaddleOCR hiện hỗ trợ 100+ ngôn ngữ ở toolkit level, và trong nhánh nhận dạng mới có model hỗ trợ riêng cho Trung phồn/giản, Anh, Nhật, vertical text, và mô hình di động cho Hàn. citeturn7view0turn10view0turn10view1

Một cảnh báo quan trọng ở cấp sản phẩm là **license risk**. Một số thành phần rất hấp dẫn cho manga pipeline lại đi kèm ràng buộc pháp lý đáng kể: `manga-image-translator`, `comic-text-detector`, `BallonsTranslator`, `mokuro`, `Koharu` đều có giấy phép copyleft/GPL ở mức cần cân nhắc rất kỹ nếu nhúng trực tiếp; `PyMuPDF` hiện là AGPL-3.0 ở repo công khai và tài liệu cũng dẫn sang licensing của Artifex. Vì vậy, Phase 3 nên **học mạnh từ các repo này**, có thể dùng như **external optional adapter / benchmark / prototype**, nhưng **không nên vội vendor-link vào permissive core** nếu chưa chốt chiến lược license. Đây không phải tư vấn pháp lý; đây là tín hiệu kỹ thuật-sản phẩm cần đưa vào backlog legal review ngay từ đầu. citeturn12view1turn12view3turn13search3turn24view1turn24view2turn35view3turn35view2

## Nghiên cứu repo, tool, dataset và kết luận kỹ thuật

Trong nhóm OCR chuyên manga, **`manga-ocr`** là repo đáng học nhất cho phần **Japanese OCR theo box/crop**. Nó nhận ảnh hoặc crop ảnh, trả về text Nhật, có Python API lẫn CLI, dùng model Vision Encoder Decoder riêng cho manga, và nhấn mạnh rõ các điểm đau của manga: vertical/horizontal text, furigana, text đè lên ảnh, nhiều font, ảnh kém chất lượng, và đặc biệt là **multi-line text trong một forward pass**. Điểm yếu lớn là phạm vi thực dụng của nó chủ yếu tập trung vào **tiếng Nhật**, và tác giả cũng ghi rõ model luôn cố gắng đọc ra chữ, kể cả khi ảnh không có chữ, tức là có nguy cơ “dream up” câu nhìn rất thật. License là Apache-2.0; local-first tốt; CLI tốt; tích hợp Python rất tốt. **Kết luận**: dùng trong MVP cho box OCR tiếng Nhật, nhưng luôn kèm confidence/QA/human correction. citeturn7view0turn12view0turn17view0

**`PaddleOCR` / `PaddleX OCR modules`** là ứng viên mạnh nhất cho **fallback OCR đa ngữ** và cho các cases Trung/Hàn/Anh/mixed-script. Ở cấp toolkit, PaddleOCR hỗ trợ 100+ ngôn ngữ; trong module nhận dạng mới, PP-OCRv5 có một line hỗ trợ chung cho **Simplified Chinese, Traditional Chinese, English, Japanese**, có nhấn mạnh **vertical text**, handwritten, pinyin và rare characters; ngoài ra còn có model di động riêng cho **Korean**. Điểm mạnh là hệ sinh thái đầy đủ, local-first, tích hợp Python tốt, và có thể dùng ở cả OCR box-level lẫn pipeline document/image. Điểm yếu là nó không chuyên manga bằng `manga-ocr`, và manga bubble với font cực dị vẫn có thể cần sửa tay. License Apache-2.0; local-first tốt; CLI/Python tốt. **Kết luận**: nên là OCR fallback chính, đồng thời là engine mặc định cho non-Japanese boxes trong MVP. citeturn7view4turn10view0turn10view1turn12view4

**`EasyOCR`** mạnh ở tính “cắm vào chạy” và hỗ trợ 80+ ngôn ngữ, phù hợp cho prototype nhanh và baseline so sánh. Nó local-first, Python integration tốt, Apache-2.0. Nhưng đây là OCR tổng quát hơn là manga-specialized OCR; vì vậy nó hợp vai trò **fallback dự phòng** hoặc baseline benchmark hơn là default engine cho module manga lâu dài. **Kết luận**: giữ dưới dạng adapter dự phòng, không chọn làm default nếu đã có `manga-ocr` + `PaddleOCR`. citeturn7view5turn12view5

**`Tesseract`** vẫn hữu ích ở vai trò tool phổ thông, local, CLI/API mạnh, Apache-2.0, và có các traineddata riêng cho **Japanese_vert, HanS_vert, HanT_vert, Hangul_vert**. Nhưng về bản chất nó vẫn là OCR engine tổng quát hơn, truyền thống hơn, và không phải lựa chọn tốt nhất cho bubble OCR manga so với `manga-ocr` hoặc các nhánh PaddleOCR mới. **Kết luận**: nên để như baseline kiểm chứng, fallback cuối, hoặc công cụ phụ cho import PDF searchable text; không nên là OCR default của manga MVP. citeturn7view6turn28search3turn28search4

Trong nhóm “pipeline all-in-one”, **`manga-image-translator`** là repo tham khảo rất giá trị vì nó đã đi hết chuỗi detection → OCR → text removal → translation → typesetting, có local batch mode, web mode, API mode, hỗ trợ custom OpenAI-compatible endpoints, có JSON mode ở một số translator, và thậm chí có chế độ chuẩn bị cho manual typesetting. Điểm mạnh là nó cho thấy một đường dây end-to-end có thật. Điểm yếu là tác giả tự ghi rõ text rendering engine còn hạn chế, detection tiếng Anh/Hàn từng là điểm yếu, vùng render dựa trên text chứ không phải bubble nên typesetting Anh không hoàn hảo, và diffusion inpainting bị ghi nhận là việc của tương lai vì chậm. License GPL-3.0 là điểm cần chú ý mạnh nếu định nhúng trực tiếp. **Kết luận**: nên học kiến trúc pipeline, config surface và retry strategy; không nên chép nguyên vào permissive core. citeturn7view1turn12view1turn18view0turn18view1turn18view2turn18view3turn18view4turn18view5

**`BallonsTranslator`** là tài liệu tham khảo tốt nhất cho **workflow GUI bán tự động**. Repo và docs mô tả one-click pipeline detection/OCR/inpainting/typesetting, nhưng đồng thời cũng cho thấy phần mạnh nhất của nó là **interactive editing**: mask editing, inpainting tool, rich text formatting, font presets, search/replace, import/export Word, và headless mode cho batch workflows. Nó còn liệt kê cụ thể các OCR backends đang dùng (`manga-ocr`, MIT models, PaddleOCR, cloud OCR), detector nguồn từ `comic-text-detector`, các inpainting backends AOT/LaMa/PatchMatch, và có thể xuất ra lớp editable cho Photoshop. Điểm yếu là license GPL-3.0 và bản chất khá monolithic cho use case app riêng. **Kết luận**: dùng như “mẫu UX và workflow review/correction”, không dùng làm lõi của Novel Translator Studio nếu mục tiêu là permissive Python core. citeturn7view2turn13search3turn19view1turn19view2turn19view3turn19view6turn19view7

**`comic-text-detector`** quan trọng vì nó không chỉ xuất **bounding boxes** mà còn hướng tới **text lines và segmentation/masks** cho manga/comics, tức là đúng thứ module cleaning/typesetting cần. Repo mô tả việc huấn luyện trên khoảng 13 nghìn ảnh anime/comic với dữ liệu Manga109, DCM, và synthetic weak supervision; đây là một tín hiệu mạnh rằng text mask và block detection chuyên truyện tranh là bài toán riêng chứ không chỉ scene text detection thông thường. Tuy nhiên repo có license GPL-3.0. **Kết luận**: đây là reference rất đáng học nhất cho auto-detection V2, nhưng nếu core app cần permissive thì nên dùng theo kiểu external adapter hoặc re-implement ý tưởng/huấn luyện lại trên data của mình. citeturn7view3turn12view3

**`Manga-Text-Segmentation`** là repo MIT đáng giá cho bài toán **pixel-level text mask**. Nó đi kèm dataset mask phát hành qua Zenodo, xuất phát từ paper “Unconstrained Text Detection in Manga: A New Dataset and Baseline”, với luận điểm rõ ràng rằng text manga là unconstrained text có kiểu dáng rất khác scene text bình thường. Điểm mạnh là license permissive và tính trực tiếp cho nhiệm vụ mask generation; điểm yếu là repo thiên nghiên cứu/notebook hơn là production-ready service. **Kết luận**: rất phù hợp để prototype mask generator hoặc để tạo feature engineering cho “Layout/OCR/clean memory” ở V2-V4. citeturn30view0turn26search22

Ở nhánh inpainting, **`LaMa`** là ứng viên kỹ thuật đáng giá nhất cho bước nâng cấp sau MVP, vì paper và repo đều nhấn mạnh khả năng xử lý **large masks**, cấu trúc hình học phức tạp, và tổng quát hóa tốt lên độ phân giải khoảng 2k dù train ở 256×256. License Apache-2.0. Đây là model nên dùng cho **Level 3 cleaning** hoặc cho các bubble/text trên nền tranh phức tạp. **Kết luận**: không phải MVP-first, nhưng là hướng nâng cấp local tốt nhất sau khi đã có mask ổn định và preview tốt. citeturn7view7turn5search1

**OpenCV** lại là tool quan trọng nhất cho **MVP cleaning** chứ không phải LaMa. Tài liệu OpenCV chỉ ra rõ `cv.inpaint()` có sẵn hai chế độ **Telea** và **Navier-Stokes**, hoạt động bằng mask và cực phù hợp cho việc vá cục bộ các vùng nhỏ, vùng bubble nền đơn giản, hoặc dọn residual pixels sau khi fill. License Apache-2.0. **Kết luận**: cleaning Level 1 và Level 2 của Novel Translator Studio nên bắt đầu bằng OpenCV trước; LaMa chỉ vào sau khi đã có workflow mask/polygon đủ tốt. citeturn32view0turn38search1

**`IOPaint`** chứng minh rằng self-hosted inpainting có thể đóng gói thành webui/CLI batch, hỗ trợ LaMa và nhiều model khác, nhưng repo đã bị archive từ tháng 8/2025. Nó vẫn hữu ích như một nguồn tham khảo cho batch masks, plugin segmentation, và self-hosted ergonomics; tuy nhiên vì đã archived, không nên coi là nền tảng lõi dài hạn. citeturn33view0

Cho import/export, tổ hợp công cụ thực dụng nhất là: **`zipfile`** của Python cho CBZ; **`rarfile`** cho CBR nhưng phải chấp nhận dependency ngoài như `unrar`, `unar`, `7zip` hoặc `bsdtar`; và một PDF backend riêng cho rasterization. `rarfile` theo interface của `zipfile` và hỗ trợ RAR3/RAR5, nhưng chính docs ghi rõ file nén phải giải qua external tool. **Kết luận**: CBZ nên là format archive ưu tiên ở MVP; CBR nên đánh dấu “optional if dependency exists”. citeturn15search0turn7view10

Với PDF, **`PyMuPDF`** rất hấp dẫn về mặt kỹ thuật: nó là thư viện hiệu năng cao, render page qua `Page.get_pixmap()`, hỗ trợ đặt DPI trực tiếp, và tutorial còn ghi nó có thể mở không chỉ PDF mà cả **CBZ/CBR/EPUB**. Tuy nhiên repo GitHub hiện hiển thị **AGPL-3.0**, và tài liệu cũng dẫn về licensing/commercial terms của Artifex. **Kết luận**: về kỹ thuật, PyMuPDF là candidate rất tốt cho prototype PDF/CBR import; về sản phẩm, đây là một license decision chứ không chỉ là quyết định kỹ thuật. Nếu app chính không muốn đi theo AGPL/commercial license của Artifex, hãy xem PyMuPDF là “cần prototype và legal review”, không phải mặc định. citeturn29search0turn29search1turn29search2turn35view3turn35view2

**`OCRmyPDF`** không phải tool core cho manga editing pipeline. Nó thêm text layer OCR vào scanned PDFs và giữ PDF searchable, rất hữu ích cho document workflows, nhưng bản chất không giải quyết bài toán box manifest, cleaning, typesetting và redraw. **Kết luận**: chỉ nên dùng nếu muốn import OCR text layer sẵn có từ PDF scan hoặc xuất “review PDF searchable” nào đó; không nên là backbone của module manga. citeturn7view8turn4search5

Về dataset và bài toán hiểu ngữ cảnh, **`Manga109`** vẫn là nguồn nền tảng cho nghiên cứu manga: bài paper mô tả 109 manga, 21.142 trang, hơn 500 nghìn annotations; `manga109api` cho biết annotations bao gồm face, body, frame, speech balloon và text, đọc qua XML. Nhưng ảnh Manga109 chủ yếu cấp phép cho **academic use**, và bản phát hành mới 2026 còn nhấn mạnh tiếp các điều kiện sử dụng. **Kết luận**: rất hợp cho benchmark, nghiên cứu detector/OCR/reading-order, nhưng tuyệt đối không dùng nó như test-pack có thể phân phối theo app hoặc plugin. citeturn9academia20turn7view11turn9search18

**`Manga109Dialog`** là nguồn tham khảo tốt nhất cho kiến trúc **speaker attribution**. Repo và paper của họ mô tả đây là dataset speaker-to-text lớn nhất cho comics, với **132.692 speaker-to-text pairs**, và mô hình SGG có xét **frame reading order** đạt accuracy trên 75%. Điều này đủ để kết luận rằng **speaker hints có thể làm được**, nhưng không nên là hard requirement của MVP; quan trọng hơn là ngay từ đầu schema phải có chỗ cho `speaker=unknown`, `speaker_source`, `speaker_confidence`, và manual assignment. citeturn7view12turn9search3

Ngoài manga Nhật, **`eBDtheque`** và **`COMICS Text+`** đáng đưa vào benchmark set cho western comics/manhwa flavored pages. eBDtheque cung cấp 100 pages với panels, balloons, characters, text lines và associations; COMICS Text+ giới thiệu các dataset detection/recognition đầu tiên cho western comics, và paper cho thấy cải thiện OCR data có ích cả cho các tác vụ hiểu truyện cấp cao hơn. **Kết luận**: benchmark nội bộ của Novel Translator Studio không nên chỉ có manga Nhật; nếu muốn module “comic/manhwa/manhua” thật sự thực dụng, cần đa miền ngay từ regression pack. citeturn6search6turn6search13turn8view0turn8view1turn8view3

Tổng hợp lại, bộ công cụ “đáng học nhất” cho Phase 3 là: **manga-ocr** cho OCR Nhật, **PaddleOCR** cho OCR đa ngữ, **comic-text-detector** và **Manga-Text-Segmentation** cho auto-detect/mask research, **BallonsTranslator** và **comic-translate** cho UX/manual-correction/end-to-end workflow reference, **OpenCV + LaMa** cho cleaning roadmap, **zipfile/rarfile + PDF rasterizer** cho ingestion, và **Manga109/Manga109Dialog/eBDtheque/COMICS Text+** cho benchmark. Trong số này, những thứ nên đi vào **MVP permissive core** trước là `manga-ocr`, `PaddleOCR`, `OpenCV`, `Pillow`, `zipfile`, `rarfile` optional, còn các repo GPL nên đóng vai trò benchmark/reference/external adapter trước khi có quyết định pháp lý. citeturn7view0turn10view0turn12view3turn30view0turn19view7turn24view0turn32view0turn15search0turn7view10

## Kiến trúc module và pipeline đề xuất

**MANGA_TECHNICAL_ARCHITECTURE.md**

Về kiến trúc, module manga nên được cắt thành các service boundary rõ ràng, bám đúng lối Phase 2: `MangaImportService`, `PageArtifactService`, `PagePreprocessService`, `BoxDetectionService`, `BoxRevisionService`, `OcrService`, `ReadingOrderService`, `SpeakerHintService`, `MangaTranslationService`, `MangaQaService`, `CleaningService`, `TypesetService`, `PreviewService`, `ExportService`, `MangaMemoryService`. GUI chỉ gọi các service này; CLI cũng gọi đúng các service này; DB chỉ lưu canonical rows và artifact references. Như vậy manga không phá Phase 2, mà chỉ là một service cluster mới dưới cùng một app shell. fileciteturn15file1L31-L32 fileciteturn15file1L81-L87

Artifact flow nên đi theo chuỗi: **source asset → normalized page asset → detector proposals → canonical boxes + versions → OCR results → translation manifest → cleaned page artifact → typeset page artifact → exports + QA report → memory updates**. Điểm mấu chốt là mỗi bước đều phải lưu **provenance**: tool/model nào chạy, version nào, tham số nào, input artifact hash nào, output artifact hash nào, run id nào, user nào xác nhận. Thiết kế này bám đúng tinh thần LAMM-T về scope/confidence/evidence/provenance thay vì “nhìn chung ảnh có vẻ dịch đúng”. fileciteturn15file0L30-L40

**MANGA_PIPELINE_SPEC.md**

**Import.** Nguồn nhập nên hỗ trợ thư mục ảnh, ảnh rời JPG/PNG/WebP, CBZ, CBR optional, PDF optional. Với thư mục ảnh/ảnh rời, app nên copy hoặc register file gốc vào artifact store, tính `sha256`, đọc width/height, EXIF/orientation, sinh normalized preview nếu cần. Với CBZ, nên dùng `zipfile` để trích trang theo thứ tự archive. Với CBR, nên đánh dấu optional do `rarfile` cần backend ngoài; nếu dependency sẵn có thì extract, nếu không thì báo error dependency-missing chứ không fail mơ hồ. Với PDF, nên rasterize từng page sang PNG lossless hoặc WebP lossless ở DPI cấu hình được; giữ **original PDF** như source artifact và **raster pages** như working artifacts. PyMuPDF có `Page.get_pixmap(dpi=...)`, nhưng license cần review; vì vậy PDF support nên đi vào MVP theo hướng “optional backend” chứ không khóa kiến trúc vào một thư viện duy nhất quá sớm. citeturn15search0turn7view10turn29search0turn29search1turn29search2turn35view3

Để detect trang trùng hoặc re-import cùng source, mỗi page nên có **hai fingerprint**: `content_sha256` cho exact duplicate và `visual_phash` hoặc `dhash` cho near-duplicate. `sha256` có thể dựa trên `hashlib`; perceptual hash có thể dùng các họ aHash/pHash/dHash. Exact hash dùng cho reproducibility; perceptual hash dùng cho “trang giống nhau nhưng khác nén/resize”. citeturn15search2turn34search0turn34search2

**Preprocess.** Nên tách ba profile preprocessing khác nhau: `ocr_preprocess`, `detection_preprocess`, `preview_preprocess`. `ocr_preprocess` có thể bao gồm grayscale/contrast/denoise/threshold cục bộ; `detection_preprocess` thường giữ màu/biên tốt hơn; `preview_preprocess` ưu tiên hiển thị và không làm hỏng ảnh. Ảnh gốc luôn giữ nguyên, mọi bản processed đều là artifact version khác. Điều này vừa phù hợp local-first vừa tránh “OCR tối ưu nhưng preview xấu” hoặc “preview đẹp nhưng OCR sai”. Các dự án open-source hiện có cũng thường tách detection, OCR, inpainting và rendering thành step/module riêng, chứ không dùng một ảnh processed chung cho tất cả. citeturn19view3turn24view1

**Text region / speech bubble detection.** Ở góc độ sản phẩm, detection nên có ba mode: `manual_only`, `ocr_proposal`, `dl_proposal`. `manual_only` là mode MVP 1 để nhập/sửa box tay hoặc import box JSON. `ocr_proposal` dùng box từ OCR engine (Paddle/EasyOCR/Tesseract) làm candidates để user duyệt. `dl_proposal` dùng detector chuyên truyện tranh hoặc adapter ngoài. `comic-text-detector` cho thấy hướng sinh ra **bbox + text lines + masks**, còn các paper về speech balloon segmentation và text box segmentation xác nhận balloon/narration box là đối tượng riêng đáng segment, không chỉ text detection thuần túy. Vì license, `comic-text-detector` nên là reference/external adapter trước; còn core app MVP nên ưu tiên manual-first + imported boxes. citeturn7view3turn12view3turn30view2

Output canonical của detection nên như sau:

```json
{
  "page_id": "pg_001",
  "boxes": [
    {
      "box_id": "bx_pg001_0001",
      "bbox": [120, 240, 330, 180],
      "polygon": [[120,240],[450,240],[450,420],[120,420]],
      "mask_ref": "artifacts/masks/pg001/bx0001.png",
      "confidence": 0.91,
      "box_type": "speech",
      "text_direction": "vertical",
      "origin": "auto",
      "detector_version": "comic_text_detector_ext@2026-05",
      "reading_order_provisional": 1
    }
  ]
}
```

**Manual box correction.** Đây phải là hạng mục hạt nhân chứ không phải “feature phụ”. GUI phải cho phép draw rectangle/polygon, drag-resize, split, merge, delete, đổi `box_type`, đổi reading order, đổi speaker, sửa raw OCR text, khóa box nào là canonical. CLI phải cho phép import/export JSON để automation hoặc chỉnh tay ngoài app. Bất kỳ correction nào cũng phải tạo **box version** mới, không overwrite câm lặng. Điều này phù hợp với thực tế của các tool như BallonsTranslator và comic-translate: dù có auto mode, manual editing/manual mode vẫn là chỗ người dùng xử lý lỗi detection/OCR/cleaning. citeturn19view1turn19view6turn25view5

Workflow sửa box nên là: auto proposal tạo `revision_no=0`; user review tạo `revision_no=1...n`; khi user bấm “accept page”, app chốt `current_version_id` của từng box. Các edit nên lưu: `before`, `after`, `change_reason`, `changed_by`, `timestamp`, `evidence_crop_ref`. Đây vừa là audit trail vừa là nguồn học cho `Manga Layout Memory`.

**OCR.** Mỗi box nên có thể chạy OCR nhiều lần với engine khác nhau. Quy tắc đề xuất là:  
`language_hint=ja` → `manga-ocr` trước;  
`language_hint=zh|en|mixed|unknown` → `PaddleOCR` trước;  
`language_hint=ko` → `PaddleOCR Korean model` trước;  
confidence thấp / mismatch script / reviewer flag → chạy engine thứ hai;  
cả hai còn mâu thuẫn → human correction;  
chỉ khi thật sự cần mới dùng vision model fallback trên crop box, không quét cả trang. Lý do là cả `manga-ocr` lẫn LVLM OCR đều có rủi ro hallucination/hallucinated text, nên fallback phải ở vùng hẹp, structured output và có cross-check. citeturn7view0turn10view0turn22view0turn17view0

Mỗi OCR result nên lưu `engine`, `engine_version`, `raw_text`, `normalized_text`, `script_guess`, `confidence`, `char_boxes_if_available`, `warnings`, `source_crop_ref`. Nếu user sửa OCR thủ công, đừng sửa đè vào result cũ; hãy tạo `ocr_result` mới với `origin=user_corrected`, đồng thời ghi một correction episode cho memory.

**Reading order.** Heuristic MVP nên được profile hóa theo series/chapter: manga Nhật mặc định **top-to-bottom, right-to-left**; western comic mặc định **top-to-bottom, left-to-right**; webtoon/manhwa mặc định **top-to-bottom single stream**; manhua để `series_profile.reading_direction` chọn được. Khi có panel boxes, reading order nên đi **panel-first rồi box-within-panel**. Nghiên cứu transcript generation cho manga cho thấy một cách làm thực dụng là: order panels trước, rồi order text boxes trong panel; với manga Nhật, previous work lẫn implementation thực dụng đều dùng prior “top-to-bottom, right-to-left”, với intra-panel heuristic dựa vào khoảng cách tới góc trên-phải; còn overlapping panels có thể giải quyết bằng DAG/topological sort thay vì recursive cuts thuần túy. citeturn27view0turn27view1

Vì reading order truyện tranh có nhiều case mơ hồ, output phải có hai trường: `reading_order_provisional` và `reading_order_final`. Manual reordering của user là nguồn học rất có giá trị, nhưng chỉ sau khi user accept mới ghi vào memory.

**Speaker / context.** MVP không cần speaker detection full-auto. Thay vào đó, mỗi box có `speaker_id nullable`, `speaker_source` (`unknown|manual|hint_model|memory`), `speaker_confidence`. Có thể cho user tạo character list của chapter/series, rồi gán box theo dropdown. Sau này V5 mới thêm `SpeakerHintService` dùng visual hints, frame context hay character proximity. Manga109Dialog đủ mạnh để nói rằng speaker attribution có giá trị thật cho personality-aware translation, nhưng vẫn chưa nên là “blocking dependency” của MVP. citeturn7view12turn9search3

**Translation by box ID.** Lõi của translation không phải “dịch trang”, mà là **dịch mảng box có ID ổn định**. Manifest gợi ý:

```json
{
  "page_id": "pg_001",
  "chapter_id": "chap_001",
  "reading_direction": "rtl_tb",
  "boxes": [
    {
      "box_id": "bx_pg001_0001",
      "order": 1,
      "type": "speech",
      "speaker_id": null,
      "raw_text": "你到底是谁？",
      "ocr_confidence": 0.93
    },
    {
      "box_id": "bx_pg001_0002",
      "order": 2,
      "type": "thought",
      "speaker_id": null,
      "raw_text": "不对，这个人很危险……",
      "ocr_confidence": 0.88
    }
  ]
}
```

Prompt translation nên luôn dùng **structured array input**, yêu cầu model trả về đúng **mảng object cùng `box_id`**, không được đổi ID, không sinh thêm box, không bỏ box. API layer phải validate `set(box_id_in) == set(box_id_out)` trước khi chấp nhận kết quả. Nếu thiếu/dư ID, retry với prompt cứng hơn; nếu vẫn lỗi, chuyển reviewer/human review. Đây là chỗ Novel Translator Studio nên nghiêng về “translation manifest” thay vì prompt tự do. Các tool như `manga-image-translator` cũng cho thấy JSON mode giúp tăng xác suất đầu ra thành công khi backend hỗ trợ; `comic-translate` lại cho thấy feeding whole-page text và, khi cần, cả page image vào model có thể cải thiện dịch. Novel Translator Studio nên kết hợp cả hai ý: **context toàn trang/chapter** nhưng **output luôn là structured per-box**. citeturn18view5turn25view6

**Translation QA / hallucination guard.** QA nên gồm hai lớp. Lớp thứ nhất là deterministic local checks: đủ box ID, không thừa/thiếu box, không thay speaker nếu speaker locked, không xuất hiện tên riêng trái glossary, không bỏ trống target khi source có text, không vượt capacity box quá ngưỡng. Lớp thứ hai là reviewer pass: một model khác hoặc cùng model ở prompt kiểm tra, chỉ nhận nhiệm vụ “so source box, context, memory bundle với target, đánh cờ warning chứ không rewrite ngay”. Vision reviewer chỉ chạy trên crop/page khi local checks báo đỏ, vì HalluText cho thấy LVLM OCR hallucination là rủi ro thật, đặc biệt khi model dựa quá nhiều vào language priors. citeturn22view0turn17view0

**Text removal / cleaning.** Nên chia làm ba cấp rõ rệt.  
**Level 1**: white fill / solid color fill cho bubble nền đơn giản, thêm mask dilation nhẹ để di chữ cũ sạch hơn.  
**Level 2**: OpenCV `cv.inpaint()` Telea/NS cho nền có texture nhẹ hoặc residual pixels.  
**Level 3**: LaMa hay model tương tự cho mask lớn/nền tranh phức tạp.  
MVP nên dừng ở **Level 1 + một phần Level 2**. Lý do là auto inpainting mạnh chỉ có giá trị khi mask đủ tốt và preview đủ tốt; nếu chưa có hai thứ đó, AI inpainting chỉ làm artwork hỏng khó giải thích hơn. Tài liệu OpenCV mô tả rõ workflow inpaint dựa trên mask; repo LaMa thì mạnh ở large-mask/high-res nên hợp V4/V5 hơn MVP3. citeturn32view0turn7view7turn5search1

**Typesetting tiếng Việt.** MVP nên làm **horizontal Vietnamese typesetting local** bằng Pillow/OpenCV, không cố giải bài toán SFX stylized redrawing ở giai đoạn đầu. Pillow cung cấp `textbbox`, `multiline_textbbox`, `textlength` và font APIs đủ để xây một wrapper/fitting loop; điều nên làm là wrapper riêng: binary search font size, greedy line-wrap theo pixel width, check chiều cao, thêm padding theo box type, rồi warning nếu không fit. Với speech bubble, nên có profile `center/center`; narration box thường `left/center`; thought bubble có thể tăng line spacing và bo padding. Các công cụ production hiện nay cũng thừa nhận text rendering là nơi dễ “chưa tới”, nên Novel Translator Studio không nên đợi “engine typesetting hoàn hảo” mới làm MVP. citeturn14search0turn14search5turn14search9turn18view2

**Preview / edit.** Mỗi page cần ít nhất năm view chuyển nhanh được: `original`, `detection_overlay`, `ocr`, `clean_preview`, `typeset_preview`, cộng thêm một mode `diff` để so original với clean/typeset. Review flow khuyến nghị là: detect → user sửa box → OCR → user sửa OCR nếu cần → translate → QA → clean preview → typeset preview → export page approved.

**Export.** Output nên hỗ trợ: thư mục ảnh đã typeset, thư mục clean-only, manifest JSON, text manifest, box CSV/JSON, QA report JSON/Markdown, và CBZ. PDF nên là optional/polished export ở giai đoạn sau, không khóa MVP. CBZ đơn giản chỉ là zip của ảnh rendered theo naming convention ổn định; đây là định dạng nên ưu tiên hơn PDF trong giai đoạn đầu vì ít phức tạp, ít license risk hơn. citeturn15search0turn24view0

## Schema dữ liệu, memory và model routing

**MANGA_DATA_SCHEMA.md**

Schema tối thiểu nên đi theo triết lý: **entity ổn định + version tables + job tables + artifact refs**.

```sql
manga_pages(
  page_id TEXT PK,
  project_id TEXT,
  chapter_id TEXT,
  page_index INTEGER,
  source_kind TEXT,              -- image|folder|cbz|cbr|pdf
  source_artifact_id TEXT,
  original_path TEXT,
  checksum_sha256 TEXT,
  visual_phash TEXT,
  width INTEGER,
  height INTEGER,
  page_label TEXT,
  import_run_id TEXT,
  status TEXT,                   -- imported|processed|reviewed|exported|error
  created_at TEXT,
  updated_at TEXT
);

manga_page_artifacts(
  artifact_id TEXT PK,
  page_id TEXT,
  artifact_kind TEXT,            -- original|normalized|ocr_pre|detect_pre|preview|clean|typeset|mask|crop
  path TEXT,
  mime_type TEXT,
  checksum_sha256 TEXT,
  meta_json TEXT,
  created_by_run_id TEXT,
  created_at TEXT
);

manga_boxes(
  box_id TEXT PK,
  page_id TEXT,
  stable_key TEXT,
  current_version_id TEXT,
  canonical_type TEXT,
  deleted INTEGER,
  created_at TEXT,
  updated_at TEXT
);

manga_box_versions(
  version_id TEXT PK,
  box_id TEXT,
  revision_no INTEGER,
  bbox_json TEXT,
  polygon_json TEXT,
  mask_artifact_id TEXT,
  box_type TEXT,                 -- speech|thought|narration|sfx|other
  text_direction TEXT,           -- horizontal|vertical|unknown
  reading_order INTEGER,
  speaker_id TEXT,
  origin TEXT,                   -- auto|manual|imported
  detector_name TEXT,
  detector_version TEXT,
  detector_confidence REAL,
  previous_version_id TEXT,
  change_reason TEXT,
  changed_by TEXT,
  created_at TEXT
);

manga_ocr_results(
  ocr_result_id TEXT PK,
  box_id TEXT,
  box_version_id TEXT,
  engine_name TEXT,
  engine_version TEXT,
  input_artifact_id TEXT,
  raw_text TEXT,
  normalized_text TEXT,
  language_hint TEXT,
  script_guess TEXT,
  confidence REAL,
  char_boxes_json TEXT,
  warnings_json TEXT,
  origin TEXT,                   -- auto|manual_import|user_corrected|reviewed
  created_at TEXT
);

manga_box_translations(
  translation_id TEXT PK,
  box_id TEXT,
  source_ocr_result_id TEXT,
  context_window_json TEXT,
  memory_bundle_ref TEXT,
  model_name TEXT,
  provider_name TEXT,
  prompt_hash TEXT,
  raw_output_json TEXT,
  translated_text TEXT,
  reviewer_status TEXT,          -- pending|passed|warn|failed
  qa_flags_json TEXT,
  approved INTEGER,
  created_at TEXT
);

manga_cleaning_jobs(
  cleaning_job_id TEXT PK,
  page_id TEXT,
  method TEXT,                   -- fill|opencv_telea|opencv_ns|lama
  input_artifact_id TEXT,
  output_artifact_id TEXT,
  mask_bundle_json TEXT,
  config_json TEXT,
  status TEXT,
  provenance_json TEXT,
  created_at TEXT
);

manga_typeset_jobs(
  typeset_job_id TEXT PK,
  page_id TEXT,
  input_artifact_id TEXT,
  output_artifact_id TEXT,
  profile_id TEXT,
  layout_json TEXT,
  overflow_flags_json TEXT,
  manual_adjustment_count INTEGER,
  status TEXT,
  provenance_json TEXT,
  created_at TEXT
);

manga_exports(
  export_id TEXT PK,
  chapter_id TEXT,
  export_kind TEXT,              -- images|cbz|pdf|manifest|qa_bundle
  output_path TEXT,
  checksum_sha256 TEXT,
  manifest_snapshot_ref TEXT,
  status TEXT,
  provenance_json TEXT,
  created_at TEXT
);

manga_visual_evidence(
  evidence_id TEXT PK,
  scope_type TEXT,               -- page|box|ocr|translation|clean|typeset|memory
  scope_id TEXT,
  artifact_id TEXT,
  note TEXT,
  evidence_json TEXT,
  created_at TEXT
);
```

Quan hệ nên rõ ràng: một page có nhiều artifact; một box có đúng một current version nhưng có nhiều box versions; một box version có thể có nhiều OCR runs; một OCR result có thể đẻ ra nhiều translation attempts; cleaning/typesetting là page-level jobs vì chúng cần phối hợp nhiều box cùng lúc. Thiết kế này cho phép rerun an toàn, không mất manual corrections, và rất hợp với service-layer architecture đã được chốt từ Phase 2. fileciteturn15file1L81-L87

Index nên có ở các tổ hợp: `(chapter_id, page_index)`, `(page_id, current_version_id)`, `(box_id, revision_no)`, `(box_id, created_at DESC)` cho OCR/translations, và `(export_kind, chapter_id)` cho export. `checksum_sha256` và `visual_phash` nên index riêng để detect trùng. `approved` và `reviewer_status` cũng nên index cho dashboard review.

**MANGA_MEMORY_SPEC.md**

Memory manga nên chia thành ba lớp: **canonical memories**, **project artifacts**, và **correction episodes**.

`Manga Layout Memory` là lane để học pattern vùng text theo series/profile: box geometry distributions, common margins, bubble/text relation, page layout archetype, panel direction. Nó được tạo khi user accept box corrections hoặc khi auto boxes được user chấp nhận nhiều lần. Scope nên là `series > chapter > project > global`. Vì nó ảnh hưởng auto-detect lần sau, nên retrieve được ở detect phase. Plugin compact export chỉ nên mang bản nén rất nhẹ, ví dụ preferred reading direction và layout priors đơn giản; không mang full visual evidence. fileciteturn15file0L30-L40

`OCR Correction Memory` lưu pattern OCR sai → đúng, kèm script/language/engine/version, ví dụ một glyph truyện nào đó hay bị đọc sai, hoặc một tên riêng lặp đi lặp lại. Lane này rất hợp với LAMM-T vì có evidence cực rõ: crop box, OCR cũ, sửa mới, user acceptance. Nó nên retrieve ở `ocr_correction` và `translation QA`, nhưng chỉ promote lên canonical khi correction lặp lại đủ số lần hoặc được user pin thủ công.

`Reading Order Memory` lưu các reorder đã được user xác nhận trên layout tương tự: series profile `rtl_tb`, webtoon `single_column_tb`, unusual two-page spreads, hoặc box types nên ưu tiên trước/sau trong cùng panel. Nó nên hỗ trợ detector/order heuristic nhưng không được overwrite manual order.

`Box Type Memory` lưu mapping ngữ cảnh hình học/visual → `speech|thought|narration|sfx|other`. Ban đầu rất yếu, nhưng sau vài trăm correction page-level nó có thể hữu ích cho auto suggestions.

`Speaker Hint Memory` không được xem là ground truth, mà là soft hints: box thường xuyên thuộc character nào, ở chapter/scene nào, với pronoun/style bundle nào. Lane này phải tích hợp với character/entity memory của LAMM-T ở Phase 1 thay vì mở ra một ontology mới cạnh tranh. fileciteturn15file0L30-L40

`Typeset Preference Memory` nên lưu theo `profile + box_type + series`: preferred font family, min/max font size, line spacing, padding, text align, color preset, outline preset, narration style, punctuation style. Đây là lane có ROI rất cao ở sản phẩm, vì nó làm lần typeset sau “giống gu đã chốt” mà không cần AI quá sâu.

`Overflow Correction Memory` ghi nhận các box từng overflow và cách user giải quyết: giảm font, tăng leading, đổi wrap policy, cho phép slight overflow, rút gọn translation, chuyển wording ngắn hơn. Lane này sẽ retrieve ở typeset review và translation QA.

`SFX Handling Memory` nên tồn tại ngay trong schema nhưng **không nên cố hoàn thiện sớm**. Ở MVP, SFX có thể để `skip`, `translate_note_only`, hoặc `manual_only`. Lane này sau này mới học policy theo series.

`Visual Evidence Memory` thực ra nên là lane gần artifact hơn là “memory tri thức thuần”. Nó lưu references tới crop OCR, mask clean, before/after typeset, reviewer notes. Mục tiêu của lane này là làm evidence cho correction và cho QA audit, không phải để plugin mang đi. Lane này rất hợp với tinh thần evidence/provenance của LAMM-T nhưng **không nên compact-export** ra plugin. fileciteturn15file0L30-L40 fileciteturn15file0L213-L217

Tóm lại:  
**Canonical memory**: Layout, OCR corrections đã xác nhận mạnh, reading-direction/profile, speaker hints đã pin, typeset preferences, overflow preferences.  
**Project artifact**: raw detections, OCR runs, masks, clean images, typeset images, reports.  
**Correction episode**: mọi edit user trên box/OCR/order/clean/typeset trước khi được promote.  
**Compact export cho plugin**: chỉ nên mang style/terminology/speaker hints đã chuẩn hóa, reading-direction và typeset presets nhẹ; **không mang** visual evidence, page fingerprints, full audit, hay memory lanes còn thô. fileciteturn15file0L213-L217

**MANGA_MODEL_ROUTING_SPEC.md**

Model routing cho manga nên là **task-level routing**, không phải “chọn một model làm mọi việc”.

- `page_preprocess`: local algorithm. Không cần LLM, không cần cloud.
- `box_detection`: local algorithm hoặc external CV model. MVP mặc định manual/imported; V2 mới thêm detector proposal.
- `ocr`: local OCR model. `manga-ocr` cho JA, `PaddleOCR` cho đa ngữ; `EasyOCR`/`Tesseract` fallback. Vision model chỉ là fallback cuối cho crop khó. citeturn7view0turn10view0turn7view5turn7view6turn22view0
- `ocr_correction`: rule-based + dictionary + optional cheap text model; không nên mặc định dùng vision model.
- `reading_order`: heuristic local + manual override; chưa cần LLM ở MVP. Có thể thêm reviewer vision cho layouts lạ ở V5. citeturn27view0turn27view1
- `speaker_hint`: default `unknown`; optional CV/SGG model ở V5. citeturn7view12turn9search3
- `manga_box_translate`: text LLM, structured JSON output bắt buộc.
- `manga_context_review`: text model mạnh hơn hoặc khác provider nếu cloud cho phép.
- `manga_hallucination_guard`: local checks trước; reviewer model sau; vision spot-check chỉ trên flagged crops. citeturn22view0turn17view0
- `text_removal`: local tools trước, diffusion/inpainting model sau. citeturn32view0turn7view7
- `typesetting`: local algorithm. Không cần LLM mặc định.
- `typeset_review`: local overflow/readability checks; optional vision review sau.
- `export_check`: local manifest/image/checksum validation.
- `layout_memory_update`: local rule engine ghi memory episodes.

Router nên lưu provenance chuẩn cho mọi task:

```json
{
  "run_id": "run_2026_05_24_abc",
  "task": "ocr",
  "provider": "local",
  "engine": "manga-ocr",
  "engine_version": "0.1.14",
  "model": "manga-ocr-default",
  "input_refs": ["artifact:crop_pg001_bx0001"],
  "config": {"lang_hint":"ja"},
  "output_ref": "ocr_result:ocr_001",
  "started_at": "...",
  "finished_at": "..."
}
```

Novel Translator Studio nên hỗ trợ adapter cho OpenAI official, OpenAI-compatible endpoints, Anthropic native, Gemini optional, local models, local OCR/detector tools; nhưng với module manga, định tuyến mặc định nên ưu tiên **local OCR/local preprocessing/local cleaning**, còn LLM chủ yếu tập trung ở **translation và review**. Điều này vừa đúng local-first, vừa giảm token/cost/privacy risk, vừa giảm tác hại khi vision model hallucinate text. fileciteturn15file1L24-L27 citeturn22view0turn17view0

## CLI, GUI, QA và bộ tiêu chí đánh giá

**MANGA_CLI_SPEC.md**

CLI manga nên bám triết lý Phase 2: automation-first, resumable, machine-readable. Command tree đề xuất:

```bash
nts manga import <path> --project <project> [--chapter <chapter>] [--json]
nts manga pages list --chapter <chapter> [--json]
nts manga preprocess --chapter <chapter> [--profile default] [--json]

nts manga boxes detect --chapter <chapter> [--mode manual_only|ocr_proposal|dl_proposal] [--json]
nts manga boxes list --chapter <chapter> [--page 1] [--json]
nts manga boxes export --chapter <chapter> --out boxes.json
nts manga boxes import boxes.json --chapter <chapter> [--replace|--merge] [--json]
nts manga boxes revise --chapter <chapter> --page 1 --ops ops.json [--json]

nts manga ocr run --chapter <chapter> [--engine auto|manga-ocr|paddleocr|easyocr|tesseract] [--json]
nts manga ocr import ocr.json --chapter <chapter> [--json]
nts manga ocr export --chapter <chapter> --out ocr.json
nts manga ocr review --chapter <chapter> [--errors-only] [--json]

nts manga order auto --chapter <chapter> [--profile rtl_tb] [--json]
nts manga order import order.json --chapter <chapter> [--json]

nts manga translate --chapter <chapter> --profile <profile> [--provider <p>] [--model <m>] [--json]
nts manga translate review --chapter <chapter> [--json]
nts manga manifest export --chapter <chapter> --out manifest.json

nts manga clean --chapter <chapter> [--level 1|2|3] [--method fill|telea|ns|lama] [--json]
nts manga typeset --chapter <chapter> [--preset default_vi] [--json]
nts manga preview --chapter <chapter> [--page 1] [--mode original|overlay|clean|typeset|diff]

nts manga export --chapter <chapter> --format images|cbz|pdf|manifest|qa [--json]
nts manga qa run --chapter <chapter> [--json]
nts manga memory update --chapter <chapter> [--json]
```

Mỗi command trong JSON mode nên trả object dạng:

```json
{
  "ok": true,
  "run_id": "run_...",
  "chapter_id": "chap_001",
  "task": "ocr",
  "status": "completed_with_warnings",
  "warnings": ["5 boxes low confidence", "2 boxes script mismatch"],
  "artifacts": [{"kind":"ocr_export","path":"..."}],
  "db_writes": {"manga_ocr_results": 124},
  "resume_token": "..."
}
```

Error codes nên gọn và ổn định: `10` input error, `20` import/decode error, `30` dependency missing, `40` validation error, `50` OCR/translation runtime error, `60` review required, `70` export failed. Mọi long-running task nên resumable theo `run_id`, và nếu một page lỗi thì CLI nên tiếp tục page khác rồi báo summary cuối cùng, trừ khi user bật `--fail-fast`.

**MANGA_GUI_WORKFLOW_SPEC.md**

MVP GUI manga nên chỉ cần một màn hình đủ rõ workflow, không cần “app scanlation hoàn chỉnh” ngay. Layout tối thiểu đề xuất: cột trái là page list; giữa là canvas; phải là tab properties/ocr/translation; đáy là warnings/jobs. Các control phải có ngay từ MVP: zoom, pan, draw rect, resize, delete, split, merge, đổi type, sửa order, sửa OCR text, sửa translation text, chuyển các layer preview. Đây là hướng thực dụng đã được các tool như BallonsTranslator, comic-translate và Koharu chứng minh: hiệu quả workflow đến từ khả năng vừa auto vừa sửa tay nhanh, không phải từ auto tuyệt đối. citeturn19view6turn25view5turn24view1

Koharu đặc biệt đáng học ở UX: local-first, object detection + OCR + inpainting + LLMs, hỗ trợ hotkeys, undo/redo, block tool, brush/eraser, headless mode, export flattened hoặc layered PSD. Dù license GPL-3.0 khiến nó không phải candidate để nhúng trực tiếp, nó là mẫu rất tốt cho **canvas interactions** và phân ranh “auto tool” với “manual correction tools”. citeturn24view1

Workflow review GUI nên là:

1. Mở chapter.  
2. Xem page với overlay boxes.  
3. Chuẩn hóa boxes.  
4. Chạy OCR hoặc xem OCR import sẵn.  
5. Sửa OCR box-by-box.  
6. Chạy translate theo chapter/page.  
7. Xem warnings QA.  
8. Preview clean.  
9. Preview typeset.  
10. Approve/export.

Hotkeys nên có: `V` select, `B` draw box, `Del` delete, `Ctrl+Z` undo, `Ctrl+Shift+Z` redo, `[` `]` đổi brush/margin nếu có tool mask, `Tab` box tiếp theo, `Shift+Tab` box trước, `Ctrl+Wheel` zoom. Undo/redo phải là yêu cầu cứng từ MVP của canvas, không được để sau.

**MANGA_QA_EVALUATION_SPEC.md**

QA metrics của manga nên chia riêng theo từng stage:

- **Detection**: box recall, false positive rate, missed text region rate, manual correction rate, % pages needing manual reorder.
- **OCR**: mean confidence, CER/WER trên sample có ground truth, OCR edit distance sau user correction, hallucinated OCR rate, language/script mismatch rate.
- **Translation**: box completeness, ID preservation rate, omission/addition rate, proper-name consistency, pronoun consistency, style match, overflow risk ratio.
- **Cleaning**: leftover text rate, mask accuracy sample score, artwork damage risk flags, manual clean touch-up rate.
- **Typesetting**: overflow rate, readability score checklist, average font size range, alignment issues, manual adjustment rate.
- **Memory**: layout correction reuse rate, OCR correction recurrence reduction, typeset preference reuse rate, % accepted auto suggestions due to learned memory.

Regression pack MVP nên nhỏ nhưng đa miền: khoảng **40–60 trang** gồm manga Nhật trắng đen, webtoon/manhwa màu dọc, manhua màu, western comic/bubble Latin. Nên có **gold boxes, gold OCR, gold order, gold translation policy notes**, không nhất thiết phải có gold redraw hoàn chỉnh cho tất cả. Dataset công khai như Manga109, Manga109Dialog, eBDtheque, COMICS Text+ rất hợp để dựng benchmark nội bộ nghiên cứu, nhưng phải tuân thủ giới hạn giấy phép và không biến thành asset phân phối sản phẩm. citeturn9academia20turn9search18turn6search6turn8view0

Human review checklist nên cực cụ thể: có thiếu box không, có box dư không, OCR có “bịa” không, người nói có bị đổi không, tên riêng có đúng glossary không, cách xưng hô có ổn với scene không, bubble có tràn không, chữ có quá nhỏ không, cleaning có ăn vào nét vẽ không, ảnh export có đúng thứ tự trang không. Review form nên lưu JSON để làm dữ liệu huấn luyện memory, không chỉ là form nhìn rồi bỏ.

## Roadmap MVP, task cho Codex và kết luận triển khai

**MANGA_MVP_IMPLEMENTATION_PLAN.md**

**MVP 0: Data foundation.** Chỉ làm import folder/CBZ, artifact registry, `manga_pages`, `manga_page_artifacts`, `manga_boxes`, import/export box JSON, checksums/fingerprints, page viewer cơ bản. Acceptance criteria: import được chapter ảnh; export được manifest rỗng; re-import không tạo duplicate exact page; page viewer mở được original/normalized page.

**MVP 1: Manual-box translation.** User hoặc file JSON nhập boxes; OCR text có thể import hoặc nhập tay; translate theo box ID; export text manifest + JSON manifest. Acceptance criteria: giữ ID tuyệt đối, retry khi model trả sai ID, lưu version/audit, export được manifest dùng lại.

**MVP 2: Semi-auto detection/OCR.** Thêm `ocr_proposal` và `dl_proposal` optional; thêm OCR engine adapters `manga-ocr`, `PaddleOCR`; thêm review low-confidence; bắt đầu update `Manga Layout Memory` và `OCR Correction Memory`. Acceptance criteria: user sửa box nhanh hơn manual-only; OCR correction được lưu episode; rerun không mất manual boxes.

**MVP 3: Simple clean/typeset.** Level 1 cleaning + một phần OpenCV inpaint; typesetter local bằng Pillow/OpenCV; preview original/clean/typeset; export images/CBZ/manifest/QA report. Acceptance criteria: bubble nền đơn giản sạch và đọc được; overflow bị cảnh báo; không phá ảnh gốc; có preview trước khi export.

**MVP 4: Inpainting/typeset improvement.** Mask tốt hơn, OpenCV mạnh hơn, thêm LaMa optional, typeset memory, manual adjust tools cho spacing/alignment/font size. Acceptance criteria: complex-background pages có đường lui tốt hơn, user adjustment rate giảm, typeset style ổn định hơn theo series/profile.

**MVP 5: Advanced automation.** Speaker hints, reading-order learning, vision QA crop-level, polished PDF export, external detector integrations được đóng gói tốt hơn. Acceptance criteria: auto suggestions hữu ích rõ rệt nhưng không ghi đè manual truth; review flow ngắn hơn; PDF/CBZ exports reproducible.

Những thứ **không nên làm sớm**: full-auto speaker detection như ground truth, diffusion redraw cho mọi page, SFX stylized redraw, perfect segmentation of every bubble tail, AI typesetting “như designer”, page-wide multimodal agents, đồng bộ plugin self-learning, hoặc ghép quá sớm các repo GPL vào core sản phẩm.

**MANGA_CODEX_TASKS.md**

Thứ tự task cho Codex nên là:

1. Tạo module `nts/manga/domain.py`, `nts/manga/models.py`, `nts/manga/repo.py`.
2. Implement `MangaImportService` + artifact registry + checksum/phash utilities.
3. Implement manifest DTOs và JSON import/export.
4. Implement `BoxRevisionService` và version tables.
5. Implement OCR adapter interface + `manga_ocr_adapter.py` + `paddleocr_adapter.py`.
6. Implement deterministic translation manifest validator.
7. Implement `MangaTranslationService` with structured output parsing and retry.
8. Implement `MangaQaService` local checks.
9. Implement `CleaningService` Level 1/2 with OpenCV.
10. Implement `TypesetService` local with Pillow.
11. Implement CLI tree `nts/cli/manga.py`.
12. Implement GUI tab `nts/gui/manga_tab.py` với canvas/bộ thao tác cơ bản.
13. Implement memory episode writers.
14. Add regression fixtures và e2e tests.

Các file/module nhiều khả năng cần có:

```text
nts/manga/import_service.py
nts/manga/artifact_store.py
nts/manga/preprocess_service.py
nts/manga/box_service.py
nts/manga/ocr_service.py
nts/manga/reading_order.py
nts/manga/translation_service.py
nts/manga/qa_service.py
nts/manga/cleaning_service.py
nts/manga/typeset_service.py
nts/manga/export_service.py
nts/manga/memory_service.py
nts/manga/schemas.py
nts/cli/manga.py
nts/gui/manga_canvas.py
nts/gui/manga_tab.py
tests/manga/test_import.py
tests/manga/test_manifest_validation.py
tests/manga/test_ocr_rerun.py
tests/manga/test_translation_id_preservation.py
tests/manga/test_typeset_overflow.py
tests/manga/test_export_cbz.py
```

Prompt ngắn gọn cho Codex nên theo kiểu tác vụ hẹp, ví dụ:  
“Implement `MangaImportService` for folder/CBZ import with SHA-256, width/height extraction, artifact registration, and resumable DB writes.”  
“Implement `ManifestValidator` that rejects missing/extra `box_id`, preserves order if locked, and emits machine-readable QA flags.”  
“Implement `PillowTypesetter` that wraps Vietnamese text into a rectangle using `textbbox`/`multiline_textbbox`, binary-searches font size, and returns overflow warnings.”  
“Implement `OpenCvCleaner` with solid-fill and `cv.inpaint` modes, taking polygon or mask input and writing artifact outputs.”

## Kết luận thực thi và các điểm cần chốt sớm

Nếu phải trả lời cực ngắn gọn “Novel Translator Studio nên bắt đầu từ đâu”, thì câu trả lời là: **bắt đầu từ Manga MVP 1**, nhưng phải dựng **MVP 0 data foundation** trước một nhịp rất ngắn. Nói cách khác: đừng chờ auto-detect hay inpainting xịn; hãy chốt trước `import → stable boxes → manifest → OCR import/edit → translate by ID → QA`. Đây là đường ngắn nhất để biến manga module thành thứ Codex có thể implement chắc tay. fileciteturn15file1L24-L27 fileciteturn15file1L81-L87

**Tool/repo đáng học nhất** là `manga-ocr`, `PaddleOCR`, `comic-text-detector`, `BallonsTranslator`, `comic-translate`, `Manga-Text-Segmentation`, `LaMa`, `Manga109Dialog`. Nhưng nếu hỏi “tool nào nên đi vào core MVP trước”, thì bộ nên vào trước là **`manga-ocr` + `PaddleOCR` + `OpenCV` + `Pillow` + `zipfile`**, còn các repo GPL nên là reference hoặc external adapter trước. citeturn7view0turn10view0turn32view0turn14search0turn15search0turn12view1turn13search3turn12view3

**OCR engine nên thử trước** là: `manga-ocr` cho box Nhật; `PaddleOCR` cho box Trung/Hàn/Anh hoặc fallback mixed-script; `EasyOCR` và `Tesseract` chỉ làm fallback/baseline. Lý do là `manga-ocr` quá khớp bài toán manga Nhật, còn PaddleOCR hiện có độ phủ ngôn ngữ và vertical-text support tốt hơn hẳn hướng generic cũ. citeturn7view0turn10view0turn7view5turn7view6

**Detection approach nên thử trước** là **manual/imported boxes trước, OCR proposals thứ hai, deep detector third**. Nếu cần một hướng V2 đáng thử nhất, hãy thử **đường `comic-text-detector`/text-mask specialized** theo kiểu optional adapter hoặc prototype riêng. Lý do là các pipeline open-source hiện tại đều cho thấy manual correction vẫn là nơi cứu project khỏi các lỗi khó chịu của detection/OCR/cleaning. citeturn12view3turn19view6turn25view5

**Clean/text removal nên làm level nào trước**: **Level 1 trước**, tức white/color fill trong bubble đơn giản, rồi thêm OpenCV inpaint cho residuals. Đừng đưa diffusion/LaMa thành mặc định đầu tiên. LaMa nên là V4/V5 cho các trang khó. citeturn32view0turn7view7turn5search1

**Typesetting nên implement bằng gì trước**: **Pillow + OpenCV**, với wrapper/fitting loop tự viết, warning overflow, manual adjust, font preset theo `speech/thought/narration`. Đừng đợi engine layout “giống Photoshop” mới ship MVP. citeturn14search0turn14search5turn14search9turn18view2

**Data schema tối thiểu cho manga** là: `manga_pages`, `manga_page_artifacts`, `manga_boxes`, `manga_box_versions`, `manga_ocr_results`, `manga_box_translations`, `manga_cleaning_jobs`, `manga_typeset_jobs`, `manga_exports`, `manga_visual_evidence`. Nếu muốn cắt bớt hơn nữa cho MVP 1, vẫn phải giữ bằng được `pages`, `artifacts`, `boxes`, `box_versions`, `ocr_results`, `translations`.

**CLI command nên làm trước**: `import`, `boxes import/export/list`, `ocr import/run`, `translate`, `manifest export`, `qa run`. Chính các command này mở đường cho automation, test harness, và Codex implement từng bước mà không phụ thuộc GUI.

**GUI screen cần trước**: chỉ cần một tab Manga với page list, canvas, properties panel, OCR panel, translation panel, warnings panel, và các thao tác box cơ bản. Đừng làm polished export UI hay speaker graph trước khi canvas review flow đủ mượt.

**Phần cần prototype kiểm chứng sớm** gồm bốn thứ:  
một là **PDF backend** vì ràng buộc license của PyMuPDF cần chốt;  
hai là **OCR quality matrix** trên các page mẫu Nhật/Trung/Hàn/Anh;  
ba là **local typesetting fit loop** với tiếng Việt có dấu;  
bốn là **cleaning masks** để xem Level 1/2 có đủ cho bubble thực tế của dự án hay không. citeturn35view3turn35view2turn7view0turn10view0turn32view0

**Phần có thể giao cho Codex implement ngay sau Phase 2** là: import/artifact registry, manifest schema, box versioning, OCR adapters, translation manifest validation, QA local checks, OpenCV cleaning căn bản, Pillow typesetter, CLI tree, và GUI canvas MVP. **Phần tuyệt đối không nên làm sớm** là full-auto speaker detection, AI redraw nền phức tạp cho mọi page, SFX stylization, và việc khóa kiến trúc vào một repo GPL/AGPL trước khi giải quyết xong legal/product constraints. citeturn9search3turn33view0turn35view3

Mức độ chắc chắn cao nhất của toàn bộ nghiên cứu này là: **Novel Translator Studio nên coi manga module là một pipeline bán tự động dựa trên manifest và memory có bằng chứng**, không phải một box “dịch ảnh truyện” all-in-one. Cách đi này vừa tương thích LAMM-T, vừa tương thích Phase 2 architecture, vừa thực tế với trạng thái của hệ open-source hiện nay. fileciteturn15file0L30-L40 fileciteturn15file1L24-L27 fileciteturn15file1L81-L87

Phần còn chưa chốt hoàn toàn là **backend PDF/CBR ưu tiên nào sẽ an toàn nhất về license cho sản phẩm**, và **bộ font đóng gói nội bộ nào sẽ là preset mặc định**; hai điểm này cần một prototype kỹ thuật ngắn cộng với review pháp lý/sản phẩm trước khi freeze implementation plan. citeturn7view10turn35view3