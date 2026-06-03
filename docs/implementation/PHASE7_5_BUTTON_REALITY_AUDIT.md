# Phase 7.5 Button Reality Audit

Generated from `VISIBLE_BUTTON_WIRING` and `apps/gui/frontend/app.js`. Browser behavior remains the source of truth; `browser manual pending` must be replaced by manual evidence before PASS.

| Page | Button text | Action id | Expected endpoint/action | Frontend handler | Backend endpoint | Current status | Tested by |
|---|---|---|---|---|---|---|---|
| Xuất file | Sao chép đường dẫn | export.copy_path | clipboard.writeText | copyOutputPath() | — | frontend-state | unit + HTTP smoke pending + browser manual pending |
| Xuất file | Xuất EPUB | export.epub | Sắp hỗ trợ: EPUB chưa bật trong Phase 7 | showToast("Xuất EPUB: Sắp hỗ trợ.") | — | placeholder | unit + browser manual pending placeholder message |
| Xuất file | Xuất gói kiểm tra | export.review_package | POST /api/projects/{project}/export | exportProject("review_package") | POST /api/projects/{project}/export | real | unit + HTTP smoke pending + browser manual pending |
| Xuất file | Xuất TXT | export.txt | POST /api/projects/{project}/export | exportProject("txt") | POST /api/projects/{project}/export | real | unit + HTTP smoke pending + browser manual pending |
| Trang chủ | Kiểm tra lại LTP | home.check_ltp | GET /api/ltp/status | dynamic/event-delegated | GET /api/ltp/status | real | unit + HTTP smoke pending + browser manual pending |
| Trang chủ | Tiếp tục dịch truyện | home.continue_project | GET /api/projects | dynamic/event-delegated | GET /api/projects | real | unit + HTTP smoke pending + browser manual pending |
| Trang chủ | Tạo dự án truyện mới | home.create_project | wizard.open | showPage("projects") | — | frontend-state | unit + HTTP smoke pending + browser manual pending |
| Trang chủ | Mở cài đặt API | home.open_settings | settings.open | showPage("settings") | — | frontend-state | unit + HTTP smoke pending + browser manual pending |
| Trang chủ | Xem bản dịch cần kiểm tra | home.review_queue | GET /api/review-queue | dynamic/event-delegated | GET /api/review-queue | real | unit + HTTP smoke pending + browser manual pending |
| Tiến độ dịch | Xem chi tiết | job.details | GET /api/jobs/{job_id} | showJobDetails() | GET /api/jobs/{job_id} | real | unit + HTTP smoke pending + browser manual pending |
| Tiến độ dịch | Mở thư mục kết quả | job.open_artifacts | POST /api/system/open-path | openJobArtifacts() | POST /api/system/open-path | real | unit + HTTP smoke pending + browser manual pending |
| Tiến độ dịch | Tiếp tục từ chỗ dừng | job.resume | POST /api/projects/{project}/translate/resume | selectPreset("resume" | POST /api/projects/{project}/translate/resume | real | unit + HTTP smoke pending + browser manual pending |
| Tiến độ dịch | Dừng sau chương hiện tại | job.stop_after_current | Sắp hỗ trợ: graceful stop nếu core hỗ trợ | showToast("Dừng sau chương hiện tại: Sắp hỗ trợ khi core hỗ trợ graceful stop.") | — | placeholder | unit + browser manual pending placeholder message |
| Manga / Ảnh | Đóng | manga.close | manga.close_modal | showPage("home") | — | frontend-state | unit + HTTP smoke pending + browser manual pending |
| Manga / Ảnh | Tìm hiểu kế hoạch | manga.plan | manga.plan_modal | openTechnical("Manga / Ảnh là placeholder Phase 7. Không có OCR | — | frontend-state | unit + HTTP smoke pending + browser manual pending |
| Dự án truyện | Xuất file | projects.export | export.with_project | showPage("export") | — | frontend-state | unit + HTTP smoke pending + browser manual pending |
| Dự án truyện | Mở dự án | projects.open | project.detail | openProjectFolder() | — | frontend-state | unit + HTTP smoke pending + browser manual pending |
| Dự án truyện | Kiểm tra bản dịch | projects.review | review.with_project | dynamic/event-delegated | — | frontend-state | unit + HTTP smoke pending + browser manual pending |
| Dự án truyện | Xem chi tiết kỹ thuật | projects.technical | technical.drawer | openProjectDetails() | — | frontend-state | unit + HTTP smoke pending + browser manual pending |
| Dự án truyện | Dịch tiếp | projects.translate | translate.with_project | { | — | frontend-state | unit + HTTP smoke pending + browser manual pending |
| Kiểm tra bản dịch | Lưu & cho hệ thống học | review.learn | POST /api/review/{item_id}/learn | saveReview(true) | POST /api/review/{item_id}/learn | real | unit + HTTP smoke pending + browser manual pending |
| Kiểm tra bản dịch | Đánh dấu đã kiểm tra | review.mark_reviewed | POST /api/review/{item_id}/mark-reviewed | markReviewed() | POST /api/review/{item_id}/mark-reviewed | real | unit + HTTP smoke pending + browser manual pending |
| Kiểm tra bản dịch | Mở đoạn cần kiểm tra | review.open_item | GET /api/review/{item_id} | loadReviewQueue() | GET /api/review/{item_id} | real | unit + HTTP smoke pending + browser manual pending |
| Kiểm tra bản dịch | Lưu chỉnh sửa | review.save | POST /api/review/{item_id}/save | saveReview(false) | POST /api/review/{item_id}/save | real | unit + HTTP smoke pending + browser manual pending |
| Kiểm tra bản dịch | Không học, chỉ lưu bản dịch | review.save_only | POST /api/review/{item_id}/save | saveReview(false) | POST /api/review/{item_id}/save | real | unit + HTTP smoke pending + browser manual pending |
| Kiểm tra bản dịch | Bỏ qua | review.skip | review.next_item | skipReviewItem() | — | frontend-state | unit + HTTP smoke pending + browser manual pending |
| Kiểm tra bản dịch | Xem chi tiết kỹ thuật | review.technical | technical.drawer | openTechnical() | — | frontend-state | unit + HTTP smoke pending + browser manual pending |
| Kiểm tra bản dịch | Xem bản gốc | review.toggle_source | review.toggle_source | toggleSource() | — | frontend-state | unit + HTTP smoke pending + browser manual pending |
| Cài đặt | Mở hướng dẫn cấu hình API | settings.api_help | help.modal | openTechnical("GUI lưu provider trong workspace/config/gui_provider.local.json. GUI-saved config ưu tiên cho lệnh chạy từ GUI; CLI vẫn dùng env/config truyền thống. API key không hiển thị lại trong response.") | — | frontend-state | unit + HTTP smoke pending + browser manual pending |
| Cài đặt | Hủy thay đổi | settings.cancel_provider | GET /api/settings/provider | loadProviderSettings(true) | GET /api/settings/provider | real | unit + HTTP smoke pending + browser manual pending |
| Cài đặt | Kiểm tra API | settings.check_api | POST /api/settings/provider/test | testProviderSettings() | POST /api/settings/provider/test | real | unit + HTTP smoke pending + browser manual pending |
| Cài đặt | Kiểm tra LTP | settings.check_ltp | GET /api/ltp/status | loadLtpStatus(true) | GET /api/ltp/status | real | unit + HTTP smoke pending + browser manual pending |
| Cài đặt | Chọn workspace | settings.choose_workspace | workspace.path_input | validateWorkspacePath() | — | frontend-state | unit + HTTP smoke pending + browser manual pending |
| Cài đặt | Xóa API key | settings.clear_api_key | POST /api/settings/provider | clearProviderApiKey() | POST /api/settings/provider | real | unit + HTTP smoke pending + browser manual pending |
| Cài đặt | Sửa cấu hình | settings.edit_provider | provider.form.enable | enableProviderEditing() | — | frontend-state | unit + HTTP smoke pending + browser manual pending |
| Cài đặt | Mở workspace | settings.open_workspace | POST /api/workspace/open-folder | openWorkspaceFolder() | POST /api/workspace/open-folder | real | unit + HTTP smoke pending + browser manual pending |
| Cài đặt | Làm mới trạng thái | settings.refresh_status | GET /api/gui/version + status endpoints | refreshAllStatus() | GET /api/gui/version + status endpoints | real | unit + HTTP smoke pending + browser manual pending |
| Cài đặt | Lưu cấu hình | settings.save_provider | POST /api/settings/provider | saveProviderSettings() | POST /api/settings/provider | real | unit + HTTP smoke pending + browser manual pending |
| Cài đặt | Khởi động LTP | settings.start_ltp | Sao chép lệnh khởi động từ cấu hình | startLtp() | — | placeholder | unit + browser manual pending placeholder message |
| Dịch truyện | Dịch 20 chương | translate.batch_20 | POST /api/projects/{project}/translate/batch | selectPreset("batch" | POST /api/projects/{project}/translate/batch | real | unit + HTTP smoke pending + browser manual pending |
| Dịch truyện | Dịch 50 chương | translate.batch_50 | POST /api/projects/{project}/translate/batch | selectPreset("batch" | POST /api/projects/{project}/translate/batch | real | unit + HTTP smoke pending + browser manual pending |
| Dịch truyện | Tạm dừng | translate.pause | Sắp hỗ trợ: tạm dừng tiến trình đang chạy | showToast("Tạm dừng: Sắp hỗ trợ.") | — | placeholder | unit + browser manual pending placeholder message |
| Dịch truyện | Tiếp tục từ chỗ dừng | translate.resume | POST /api/projects/{project}/translate/resume | selectPreset("resume" | POST /api/projects/{project}/translate/resume | real | unit + HTTP smoke pending + browser manual pending |
| Dịch truyện | Bắt đầu dịch | translate.start | POST /api/projects/{project}/translate/batch | translateSelectedPreset() | POST /api/projects/{project}/translate/batch | real | unit + HTTP smoke pending + browser manual pending |
| Dịch truyện | Dừng sau chương hiện tại | translate.stop_after_current | Sắp hỗ trợ khi core hỗ trợ graceful stop | showToast("Dừng sau chương hiện tại: Sắp hỗ trợ khi core hỗ trợ graceful stop.") | — | placeholder | unit + browser manual pending placeholder message |
| Dịch truyện | Xem chi tiết kỹ thuật | translate.technical | technical.drawer | openTechnical() | — | frontend-state | unit + HTTP smoke pending + browser manual pending |
| Dịch truyện | Dịch thử 1 chương | translate.trial_1 | POST /api/projects/{project}/translate/trial | selectPreset("trial" | POST /api/projects/{project}/translate/trial | real | unit + HTTP smoke pending + browser manual pending |
| Dịch truyện | Dịch thử 10 chương | translate.trial_10 | POST /api/projects/{project}/translate/trial | selectPreset("trial" | POST /api/projects/{project}/translate/trial | real | unit + HTTP smoke pending + browser manual pending |
| Dịch truyện | Dịch thử 3 chương | translate.trial_3 | POST /api/projects/{project}/translate/trial | selectPreset("trial" | POST /api/projects/{project}/translate/trial | real | unit + HTTP smoke pending + browser manual pending |
| Tạo dự án | Chọn file | wizard.choose_file | file.path_input | showToast("Chọn file: nhập đường dẫn trong bước tạo dự án.") | — | frontend-state | unit + HTTP smoke pending + browser manual pending |
| Tạo dự án | Tạo dự án | wizard.create | POST /api/projects/import | createProject() | POST /api/projects/import | real | unit + HTTP smoke pending + browser manual pending |
| Tạo dự án | Tiếp tục | wizard.next | wizard.next_step | advanceWizard() | — | frontend-state | unit + HTTP smoke pending + browser manual pending |
| Tạo dự án | Nhận diện tự động | wizard.nlp_detect | POST /api/projects/{project}/nlp/cache-build | projectAction("/nlp/cache-build" | POST /api/projects/{project}/nlp/cache-build | real | unit + HTTP smoke pending + browser manual pending |
| Tạo dự án | Quét chương | wizard.scan_chapters | POST /api/projects/{project}/scan-chapters | projectAction("/scan-chapters" | POST /api/projects/{project}/scan-chapters | real | unit + HTTP smoke pending + browser manual pending |

## Remaining Placeholders
- `export.epub` — Xuất EPUB: Sắp hỗ trợ: EPUB chưa bật trong Phase 7
- `job.stop_after_current` — Dừng sau chương hiện tại: Sắp hỗ trợ: graceful stop nếu core hỗ trợ
- `settings.start_ltp` — Khởi động LTP: Sao chép lệnh khởi động từ cấu hình
- `translate.pause` — Tạm dừng: Sắp hỗ trợ: tạm dừng tiến trình đang chạy
- `translate.stop_after_current` — Dừng sau chương hiện tại: Sắp hỗ trợ khi core hỗ trợ graceful stop
