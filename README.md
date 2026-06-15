# Riot Esports Data Ingestion

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![AsyncIO](https://img.shields.io/badge/asyncio-aiohttp-green)
![Riot API](https://img.shields.io/badge/Riot_API-Match--V5_%7C_League--V4-red)
![Tests](https://img.shields.io/badge/tests-7_passed-brightgreen)

Crawler bất đồng bộ dùng Riot Games API để thu thập dữ liệu người chơi, xếp hạng,
trận đấu và timeline. Dự án ưu tiên lưu JSON gốc trước, hỗ trợ chạy nối tiếp an
toàn bằng checkpoint, sau đó chuyển đổi dữ liệu thành CSV phục vụ phân tích.

Mặc định dự án hướng đến máy chủ Việt Nam (`vn2`), nhưng các lệnh thông thường
vẫn hỗ trợ nhiều platform region khác. Riêng chế độ `crawl-overnight` được khóa
ở `vn2`.

## Mục lục

- [Tính năng chính](#tính-năng-chính)
- [Kiến trúc và luồng dữ liệu](#kiến-trúc-và-luồng-dữ-liệu)
- [Cấu trúc dự án](#cấu-trúc-dự-án)
- [Yêu cầu hệ thống](#yêu-cầu-hệ-thống)
- [Cài đặt nhanh](#cài-đặt-nhanh)
- [Cấu hình](#cấu-hình)
- [Cách sử dụng](#cách-sử-dụng)
- [Dữ liệu đầu ra](#dữ-liệu-đầu-ra)
- [Checkpoint và khả năng chạy lại](#checkpoint-và-khả-năng-chạy-lại)
- [Rate limit, retry và ước tính công suất](#rate-limit-retry-và-ước-tính-công-suất)
- [Kiểm thử](#kiểm-thử)
- [Xử lý sự cố](#xử-lý-sự-cố)
- [Lưu ý bảo mật và sử dụng API](#lưu-ý-bảo-mật-và-sử-dụng-api)

## Tính năng chính

- Thu thập dữ liệu qua Riot Summoner-V4, League-V4 và Match-V5.
- Hỗ trợ crawl theo danh sách PUUID hoặc seed người chơi từ leaderboard.
- Lưu nguyên JSON phản hồi trước khi validate và chuyển đổi.
- Bỏ qua match/timeline đã có bằng cả raw file và checkpoint.
- Tự khôi phục checkpoint từ các raw file hiện có khi khởi động.
- Giới hạn request theo toàn cục, region và từng nhóm API method.
- Đọc rate-limit header của Riot để giảm tốc trước khi gặp HTTP `429`.
- Retry theo exponential backoff cho `429`, `503`, `504`.
- Circuit breaker tạm dừng region khi lỗi retryable xảy ra liên tiếp.
- Chạy theo batch trong chế độ overnight và tự dừng khi hết thời gian.
- Xuất CSV có schema cố định, sắp xếp và loại bản ghi trùng lặp.
- Có thể dựng lại toàn bộ CSV từ raw JSON mà không gọi Riot API.

## Kiến trúc và luồng dữ liệu

```text
PUUID truyền từ CLI hoặc League-V4 leaderboard
                         |
                         v
                 Summoner-V4 profile
                         |
                         +--------> League-V4 ranked data
                         |
                         v
                 Match-V5 match IDs
                         |
                  kiểm tra checkpoint
                  và raw file hiện có
                         |
                         v
                 Match-V5 match detail
                         |
                         v
              output/raw/matches/*.json
                         |
                         v
                 Match-V5 timeline
                         |
                         v
             output/raw/timelines/*.json
                         |
                         v
                 python main.py process
                         |
          +--------------+---------------+
          |              |               |
     players.csv     matches.csv     timelines.csv
                         |
                     ranked.csv
```

Các thành phần chính:

| Thành phần | Trách nhiệm |
|---|---|
| `RiotClient` | Gọi HTTP bất đồng bộ, gắn API key, retry và xử lý lỗi |
| `RiotRateLimiter` | Giới hạn concurrency, request/phút, cooldown và circuit breaker |
| `SummonerService` | Thu thập và lưu hồ sơ summoner |
| `RankedService` | Thu thập ranked entry và leaderboard |
| `MatchService` | Lấy match ID, lưu match detail mới và tạo record phân tích |
| `TimelineService` | Lưu timeline mới và chuẩn hóa event |
| `CheckpointManager` | Theo dõi match/timeline đã lưu bằng JSON checkpoint |
| `process_raw_to_csv()` | Đọc raw JSON, validate bằng Pydantic và xuất CSV |

## Cấu trúc dự án

```text
.
|-- crawler/
|   |-- api/
|   |   `-- riot_client.py
|   |-- config/
|   |   `-- settings.py
|   |-- schemas/
|   |   `-- models.py
|   |-- services/
|   |   |-- match_service.py
|   |   |-- ranked_service.py
|   |   |-- summoner_service.py
|   |   `-- timeline_service.py
|   `-- utils/
|       |-- checkpoint.py
|       |-- helpers.py
|       |-- logger.py
|       |-- rate_limiter.py
|       `-- retry.py
|-- checkpoints/              # matches.json và timelines.json khi chạy
|-- logs/                     # log riêng cho từng lần chạy
|-- output/
|   |-- raw/
|   |   |-- matches/
|   |   |-- ranked/
|   |   |-- summoners/
|   |   `-- timelines/
|   `-- processed/            # bốn file CSV được sinh ra
|-- tests/
|-- main.py                   # CLI và bước xử lý CSV
|-- overnight_safe.ps1        # wrapper PowerShell cho crawl qua đêm
|-- requirements.txt
|-- pytest.ini
`-- .env.example
```

`output/`, `checkpoints/` và `logs/` được tạo tự động nếu chưa tồn tại. Dữ liệu
sinh ra trong các thư mục này không được commit theo cấu hình `.gitignore`.

## Yêu cầu hệ thống

- Python 3.10 trở lên.
- Riot Games API key còn hiệu lực.
- Kết nối mạng đến `*.api.riotgames.com`.
- Dung lượng đĩa đủ lớn nếu thu timeline. Timeline JSON và CSV có thể lớn hơn
  đáng kể so với match detail.
- PowerShell và quyền thay đổi power plan nếu dùng `overnight_safe.ps1`.

## Cài đặt nhanh

```bash
git clone https://github.com/kung-da/Riot-esports-data-ingestion.git
cd Riot-esports-data-ingestion

python -m venv .venv
```

Kích hoạt virtual environment:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS/Linux
source .venv/bin/activate
```

Cài dependency:

```bash
python -m pip install -r requirements.txt
```

Tạo file cấu hình:

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

```bash
# macOS/Linux
cp .env.example .env
```

Sau đó cập nhật API key trong `.env`:

```env
RIOT_API_KEY=RGAPI-your-api-key
```

Kiểm tra CLI:

```bash
python main.py --help
```

## Cấu hình

Cấu hình được đọc từ biến môi trường và file `.env` bằng
`pydantic-settings`. Biến môi trường hệ thống có thể ghi đè giá trị trong file.

### Kết nối và region

| Biến | Giá trị trong `.env.example` | Ý nghĩa |
|---|---:|---|
| `RIOT_API_KEY` | bắt buộc | Riot API key |
| `DEFAULT_PLATFORM_REGION` | `vn2` | Platform shard mặc định |
| `DEFAULT_ROUTING_REGION` | rỗng | Ghi đè routing region; để rỗng để tự ánh xạ |
| `REGIONS` | `vn2` | Danh sách platform region, phân tách bằng dấu phẩy |
| `REQUEST_TIMEOUT_SECONDS` | `30` | Tổng timeout cho một request |
| `USER_AGENT` | `RiotDataCrawler/1.0...` | User-Agent gửi đến Riot API |

Ánh xạ platform sang routing region:

| Routing region | Platform region |
|---|---|
| `americas` | `br1`, `la1`, `la2`, `na1` |
| `asia` | `jp1`, `kr` |
| `europe` | `eun1`, `euw1`, `ru`, `tr1` |
| `sea` | `oc1`, `ph2`, `sg2`, `th2`, `tw2`, `vn2` |

### Rate limit và retry

| Biến | Giá trị trong `.env.example` | Ý nghĩa |
|---|---:|---|
| `MAX_CONCURRENCY` | `5` | Số request đồng thời tối đa |
| `REQUESTS_PER_MINUTE` | `45` | Giới hạn request toàn cục mỗi phút |
| `METHOD_REQUESTS_PER_MINUTE` | `40` | Giới hạn mỗi nhóm API method mỗi phút |
| `REQUEST_SLEEP_MIN_SECONDS` | `0.8` | Khoảng nghỉ ngẫu nhiên tối thiểu |
| `REQUEST_SLEEP_MAX_SECONDS` | `1.5` | Khoảng nghỉ ngẫu nhiên tối đa |
| `RETRY_ATTEMPTS` | `5` | Số lần thử tối đa |
| `RETRY_BASE_DELAY_SECONDS` | `1.0` | Delay nền cho exponential backoff |
| `RETRY_MAX_DELAY_SECONDS` | `60` | Delay retry tối đa |
| `CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `5` | Số lỗi liên tiếp trước khi mở circuit |
| `CIRCUIT_BREAKER_COOLDOWN_SECONDS` | `300` | Thời gian circuit breaker tạm dừng |
| `TIMELINE_EXTRA_DELAY_SECONDS` | `2.0` | Delay thêm trước mỗi timeline request |

Ứng dụng tự ép các ngưỡng an toàn:

- `MAX_CONCURRENCY` không vượt quá `5`.
- `REQUESTS_PER_MINUTE` không vượt quá `50`.
- `METHOD_REQUESTS_PER_MINUTE` không vượt quá `40`.
- `REQUEST_SLEEP_MIN_SECONDS` không thấp hơn `1.0`.
- `RETRY_ATTEMPTS` không vượt quá `5`.
- `TIMELINE_EXTRA_DELAY_SECONDS` không thấp hơn `2.0`.

Vì vậy, khi dùng nguyên `.env.example`, giá trị `REQUEST_SLEEP_MIN_SECONDS=0.8`
sẽ được nâng lên `1.0` lúc chạy.

### Overnight và đường dẫn

| Biến | Giá trị trong `.env.example` | Ý nghĩa |
|---|---:|---|
| `DEFAULT_MATCH_COUNT` | `10` | Số match ID mặc định cho mỗi PUUID |
| `OVERNIGHT_TARGET_MATCHES` | `20000` | Mục tiêu match mới |
| `OVERNIGHT_HOURS` | `8` | Thời gian chạy tối đa |
| `OVERNIGHT_BATCH_SIZE` | `1000` | Số match mới trước khi process và nghỉ |
| `OVERNIGHT_BATCH_SLEEP_MIN_SECONDS` | `480` | Nghỉ batch tối thiểu |
| `OVERNIGHT_BATCH_SLEEP_MAX_SECONDS` | `900` | Nghỉ batch tối đa |
| `OVERNIGHT_LEADERBOARD_LIMIT_PER_TIER` | `50` | Số seed tối đa trên mỗi tier |
| `OVERNIGHT_MATCH_COUNT_PER_PUUID` | `20` | Kích thước trang match history |
| `OVERNIGHT_INCLUDE_TIMELINES` | `true` | Có thu timeline trong overnight hay không |
| `OVERNIGHT_TIERS` | `CHALLENGER,...,DIAMOND:I` | Kế hoạch tier để lấy seed |
| `OUTPUT_DIR` | `output` | Thư mục dữ liệu |
| `CHECKPOINT_DIR` | `checkpoints` | Thư mục checkpoint |
| `LOG_DIR` | `logs` | Thư mục log |
| `LOG_LEVEL` | `INFO` | Mức log |

`OVERNIGHT_BATCH_SIZE` được ép trong khoảng `100` đến `1200`, còn thời gian nghỉ
tối thiểu giữa các batch được ép ít nhất `480` giây.

## Cách sử dụng

CLI có bốn subcommand:

```text
crawl-puuids
crawl-leaderboard
crawl-overnight
process
```

Sau mỗi lệnh crawl hoàn tất, chương trình tự chạy bước tạo CSV.

### 1. Crawl theo PUUID

Thu thập summoner, ranked entry, match detail và timeline cho một hoặc nhiều
PUUID:

```bash
python main.py crawl-puuids \
  --puuids <PUUID_1> <PUUID_2> \
  --platform-region vn2 \
  --match-count 20
```

Chỉ lấy match detail, bỏ ranked và timeline:

```bash
python main.py crawl-puuids \
  --puuids <PUUID> \
  --skip-ranked \
  --skip-timelines
```

Lọc Ranked Solo/Duo bằng queue ID `420`:

```bash
python main.py crawl-puuids \
  --puuids <PUUID> \
  --queue 420 \
  --match-count 20
```

| Tham số | Mặc định | Mô tả |
|---|---|---|
| `--puuids` | bắt buộc | Một hoặc nhiều PUUID |
| `--platform-region` | `.env` | Platform shard |
| `--routing-region` | tự ánh xạ | Match-V5 routing region |
| `--match-count` | `DEFAULT_MATCH_COUNT` | Số match ID trên mỗi PUUID |
| `--start` | `0` | Offset trong lịch sử đấu |
| `--queue` | không lọc | Riot queue ID |
| `--skip-ranked` | tắt | Không gọi ranked endpoint |
| `--skip-timelines` | tắt | Không thu timeline |

Lưu ý: timeline trong lệnh này chỉ được gọi cho các match detail vừa tải mới.
Nếu raw match đã tồn tại nhưng timeline tương ứng bị thiếu, match sẽ bị skip và
timeline đó không được tự backfill bởi lệnh `crawl-puuids`.

### 2. Crawl từ leaderboard

Lấy người chơi từ một tier:

```bash
python main.py crawl-leaderboard \
  --platform-region vn2 \
  --tier CHALLENGER \
  --limit 50 \
  --match-count 20
```

Lấy seed theo toàn bộ kế hoạch `OVERNIGHT_TIERS`:

```bash
python main.py crawl-leaderboard \
  --platform-region vn2 \
  --tier ALL \
  --limit 50 \
  --match-count 20
```

Với `--tier ALL` hoặc `TOP`, `--limit` được áp dụng cho từng tier, không phải
tổng số người chơi của tất cả tier.

| Tham số | Mặc định | Mô tả |
|---|---|---|
| `--platform-region` | `.env` | Platform shard |
| `--routing-region` | tự ánh xạ | Match-V5 routing region |
| `--queue` | `RANKED_SOLO_5x5` | Loại ranked queue |
| `--tier` | `ALL` | Tier hoặc `ALL`/`TOP` |
| `--division` | `I` | Division cho tier thường |
| `--limit` | `25` | Số entry tối đa trên mỗi leaderboard được đọc |
| `--match-count` | `DEFAULT_MATCH_COUNT` | Số match trên mỗi PUUID |
| `--skip-timelines` | tắt | Không thu timeline |

### 3. Crawl qua đêm

Chế độ này:

- Chỉ chấp nhận `--platform-region vn2`.
- Duyệt seed theo vòng tròn và tăng offset lịch sử đấu sau mỗi lượt.
- Dừng khi đạt mục tiêu match mới hoặc hết thời gian.
- Process CSV sau mỗi batch.
- Nghỉ ngẫu nhiên giữa các batch nếu deadline còn đủ xa.

Ví dụ thu cả match detail và timeline:

```bash
python main.py crawl-overnight \
  --hours 8 \
  --target-matches 8000 \
  --platform-region vn2
```

Chỉ thu match detail:

```bash
python main.py crawl-overnight \
  --hours 8 \
  --target-matches 15000 \
  --skip-timelines
```

Tùy chỉnh tier:

```bash
python main.py crawl-overnight \
  --hours 10 \
  --target-matches 10000 \
  --tiers CHALLENGER GRANDMASTER MASTER DIAMOND:I \
  --leaderboard-limit 50
```

Dùng PUUID có sẵn thay cho leaderboard discovery:

```bash
python main.py crawl-overnight \
  --hours 8 \
  --target-matches 10000 \
  --puuids <PUUID_1> <PUUID_2>
```

| Tham số | Mặc định | Mô tả |
|---|---|---|
| `--hours` | `OVERNIGHT_HOURS` | Thời gian chạy tối đa |
| `--target-matches` | `OVERNIGHT_TARGET_MATCHES` | Mục tiêu match mới |
| `--platform-region` | `vn2` | Chỉ `vn2` được hỗ trợ |
| `--leaderboard-limit` | `.env` | Seed tối đa trên mỗi tier |
| `--match-count` | `.env` | Kích thước trang lịch sử mỗi PUUID |
| `--batch-size` | `.env` | Match mới trên mỗi batch |
| `--batch-sleep-min` | `.env` | Thời gian nghỉ batch tối thiểu |
| `--batch-sleep-max` | `.env` | Thời gian nghỉ batch tối đa |
| `--tiers` | `.env` | Danh sách tier; division dùng cú pháp `TIER:I` |
| `--puuids` | không có | Seed trực tiếp, bỏ bước leaderboard |
| `--queue` | `RANKED_SOLO_5x5` | Queue dùng để tìm seed |
| `--match-queue` | không lọc | Queue ID dùng khi lấy match history |
| `--skip-timelines` | tắt | Không thu timeline |

#### PowerShell wrapper

```powershell
.\overnight_safe.ps1
```

Script hiện tại:

- Đặt standby timeout khi cắm điện thành `0`.
- Chạy `crawl-overnight` trong 8 giờ với mục tiêu 20.000 match.
- Truyền `--skip-timelines`, nên chỉ thu match detail.
- Ghi thêm một file log wrapper trong `logs/`.
- Chạy lại `process` sau crawl.
- Khôi phục standby timeout khi cắm điện về 15 phút.

Trước khi dùng trên máy khác, cần sửa đường dẫn `cd
D:\Project\Riot-esports-data-ingestion` đang được ghi cố định trong script.
Lệnh `powercfg` cũng có thể yêu cầu mở PowerShell với quyền phù hợp.

### 4. Dựng lại CSV

```bash
python main.py process
```

Lệnh này chỉ đọc `output/raw/**/*.json`, không khởi tạo `RiotClient`, không cần
API key và không gọi mạng. JSON lỗi hoặc không đúng schema sẽ được bỏ qua và ghi
vào log.

## Dữ liệu đầu ra

### Raw JSON

| Đường dẫn | Nội dung |
|---|---|
| `output/raw/summoners/*.json` | Phản hồi Summoner-V4 |
| `output/raw/ranked/*.json` | Ranked entry hoặc leaderboard League-V4 |
| `output/raw/matches/*.json` | Match detail Match-V5 |
| `output/raw/timelines/*.json` | Timeline Match-V5 |

Raw payload được lưu với định dạng UTF-8, indent và key được sắp xếp. Các model
Pydantic cho phép field ngoài schema để không làm mất dữ liệu Riot bổ sung.

### `players.csv`

Mỗi dòng là một người chơi trong một trận:

```text
match_id, participant_id, puuid, summoner_name,
riot_id_game_name, riot_id_tagline,
champion_id, champion_name,
kills, deaths, assists, kda,
damage_dealt, gold_earned, vision_score,
win, team_id, position, team_position, individual_position
```

`kda` được tính bằng `(kills + assists) / deaths`; nếu `deaths = 0`, giá trị là
`kills + assists`.

### `matches.csv`

Mỗi dòng là một trận:

```text
match_id, game_creation, game_start_timestamp, game_end_timestamp,
game_duration, queue_id, game_version, map_id, platform_id,
game_mode, game_type, winning_team, participant_count
```

### `ranked.csv`

Mỗi dòng là một ranked entry:

```text
puuid, summoner_id, queue_type, tier, rank, league_points,
wins, losses, win_rate, veteran, inactive, fresh_blood, hot_streak
```

`win_rate` được tính bằng `wins / (wins + losses)`.

### `timelines.csv`

Mỗi dòng là một event đã chuẩn hóa:

```text
match_id, timestamp, participant_id, event_type, event_category, events,
related_participant_id, position_x, position_y, item_id, skill_slot,
monster_type, monster_sub_type, building_type, lane_type, ward_type,
killer_id, victim_id, creator_id, assisting_participant_ids
```

Event `CHAMPION_KILL` được tách thành nhiều dòng `kill`, `death` và `assist`.
Các event phổ biến khác được chuẩn hóa thành `ward_place`, `objective`,
`building`, `item_purchase`, `skill_level`, `level_up` và các category tương
ứng.

Tất cả CSV:

- Luôn có header, kể cả khi không có dữ liệu.
- Được sắp xếp theo khóa định danh.
- Được loại trùng theo khóa phù hợp với từng file.
- Được ghi đè hoàn toàn mỗi lần chạy `process`.

## Checkpoint và khả năng chạy lại

Checkpoint nằm tại:

```text
checkpoints/matches.json
checkpoints/timelines.json
```

Trước khi gọi match detail hoặc timeline, chương trình kiểm tra:

1. Raw JSON tương ứng đã tồn tại hay chưa.
2. Match ID đã có trong checkpoint hay chưa.

Chỉ khi cả hai đều chưa có thì request mới được gửi. Sau khi raw JSON được lưu
và validate thành công, ID được ghi ngay vào checkpoint bằng file tạm rồi
`replace`, giảm nguy cơ checkpoint bị ghi dở.

Khi khởi động một lệnh crawl, `hydrate_from_raw_files()` quét raw match và
timeline hiện có để bổ sung lại checkpoint. Vì vậy có thể xóa checkpoint và tạo
lại từ raw data.

Một raw payload không hợp lệ vẫn được giữ để điều tra, nhưng không được đánh dấu
processed. Tuy nhiên, lần chạy sau vẫn skip request đó vì raw file đã tồn tại.

## Rate limit, retry và ước tính công suất

Limiter quản lý đồng thời:

- Semaphore toàn cục.
- Semaphore theo region.
- Cửa sổ 60 giây toàn cục.
- Cửa sổ 60 giây theo region.
- Cửa sổ 60 giây theo method.
- Khoảng nghỉ ngẫu nhiên giữa các request.

Khi nhận `429`, throughput theo region giảm và cooldown được áp dụng theo
`Retry-After` hoặc backoff. Khi header Riot cho thấy mức sử dụng từ 85% trở lên,
client chủ động giảm tốc trong thời gian ngắn.

Chế độ overnight dùng công thức ước tính:

```text
capacity = hours * 60 * requests_per_minute * 0.9 / requests_per_match
```

Trong đó `requests_per_match` bằng `2` khi có timeline và `1` khi bỏ timeline.
Đây chỉ là trần lý thuyết của phần match/timeline. Thực tế còn request
leaderboard, summoner, retry, timeline delay, batch sleep, match trùng và thời
gian xử lý CSV.

Nếu mục tiêu vượt ước tính, crawler chỉ ghi warning và vẫn ưu tiên dừng theo
deadline; chương trình không tăng tốc để cố đạt mục tiêu.

## Kiểm thử

Chạy toàn bộ test:

```bash
pytest -q
```

Các test hiện có kiểm tra:

- Rate limiter hoàn thành request slot và ép ngưỡng không an toàn.
- Checkpoint skip raw match đã tồn tại.
- Checkpoint ghi nhận match mới.
- Chuyển đổi match thành match/player record.
- Tách timeline kill thành kill/death/assist.
- Validate summoner payload không có encrypted summoner ID.

Trạng thái tại lần rà soát README:

```text
7 passed
```

Test hiện tại không gọi Riot API thật và chưa bao phủ end-to-end network crawl.

## Xử lý sự cố

### `RIOT_API_KEY is required`

Đảm bảo `.env` tồn tại ở thư mục gốc và có:

```env
RIOT_API_KEY=RGAPI-...
```

Development key của Riot thường có thời hạn ngắn; thay key mới không ảnh hưởng
raw data hoặc checkpoint.

### HTTP `401` hoặc `403`

- Kiểm tra key đã hết hạn hay chưa.
- Kiểm tra sản phẩm API có quyền truy cập endpoint cần dùng.
- Không thêm dấu nháy hoặc khoảng trắng thừa vào key.

### HTTP `429`

Crawler sẽ tự cooldown và retry. Nếu lỗi lặp lại:

- Giảm `REQUESTS_PER_MINUTE`.
- Giảm `METHOD_REQUESTS_PER_MINUTE`.
- Tăng khoảng nghỉ request.
- Tránh chạy nhiều tiến trình crawler dùng cùng API key.

### CSV rất chậm hoặc tốn RAM

`process` hiện đọc toàn bộ JSON cần thiết vào bộ nhớ và dùng pandas để dựng lại
toàn bộ CSV. Với dataset lớn:

- Đảm bảo còn đủ RAM và dung lượng đĩa.
- Chạy `process` riêng sau khi crawl thay vì quá thường xuyên.
- Có thể dùng `--skip-timelines` nếu không cần phân tích event.

### Tên người chơi bị lỗi ký tự khi mở CSV

CSV được ghi UTF-8. Hãy chọn UTF-8 khi import vào Excel hoặc công cụ BI thay vì
để phần mềm tự đoán encoding.

### Raw có nhưng CSV thiếu record

Kiểm tra log của lệnh `process`. Payload sai JSON hoặc không đáp ứng schema
Pydantic sẽ bị bỏ qua, nhưng raw file vẫn được giữ để điều tra.

## Lưu ý bảo mật và sử dụng API

- Không commit `.env` hoặc API key thật.
- Không đưa API key vào command line, log hoặc ảnh chụp màn hình.
- Tuân thủ Riot Games API Terms, rate limit và chính sách sử dụng dữ liệu.
- Không chạy nhiều instance với cùng key nếu chưa điều phối rate limit chung.
- Repository hiện không có file `LICENSE`; không nên mặc định mã nguồn đã được
  cấp giấy phép sử dụng lại chỉ dựa trên việc repository có thể truy cập.
