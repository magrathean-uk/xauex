# GBPJPY source list

| Tier | Name | Kind | Auto | Why | URL |
|---|---|---|---|---|---|
| 1 | Bank of England news RSS | rss | yes | BoE announcements and policy-related releases are immediate drivers of sterling repricing. | https://www.bankofengland.co.uk/rss/news |
| 1 | Bank of England speeches RSS | rss | yes | MPC tone and guidance changes often show up in speeches before the market fully reprices them. | https://www.bankofengland.co.uk/rss/speeches |
| 1 | Bank of England statistics RSS | rss | yes | Helpful for official releases that matter to rates and liquidity conditions in sterling markets. | https://www.bankofengland.co.uk/rss/statistics |
| 1 | Bank Rate latest decision page | html | yes | Current Bank Rate and the policy narrative are primary inputs for GBPJPY rate-differential thinking. | https://www.bankofengland.co.uk/monetary-policy/the-interest-rate-bank-rate.%C2%A0 |
| 1 | Bank of England MPC dates | html | yes | Knowing when the next decision is due helps the swarm weight event risk and narrative urgency. | https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates |
| 2 | Bank of England database | html | manual | Useful for series-level adapters such as official rates or market expectations. | https://www.bankofengland.co.uk/boeapps/database/ |
| 1 | ONS consumer price inflation bulletin | html | yes | UK inflation surprises materially affect BoE pricing and sterling. | https://www.ons.gov.uk/economy/inflationandpriceindices/bulletins/consumerpriceinflation/latest |
| 2 | ONS CPI dataset page | html | yes | Useful when you want the raw CPI tables behind the bulletin rather than the narrative summary. | https://www.ons.gov.uk/economy/inflationandpriceindices/datasets/consumerpriceindices |
| 1 | ONS GDP monthly estimate | html | yes | Growth data matters for the BoE path and for the relative attractiveness of sterling versus yen. | https://www.ons.gov.uk/economy/grossdomesticproductgdp/bulletins/gdpmonthlyestimateuk/latest |
| 1 | ONS labour market overview | html | yes | Labor-market tightness is a core BoE input and therefore a GBPJPY driver. | https://www.ons.gov.uk/employmentandlabourmarket/peopleinwork/employmentandemployeetypes/bulletins/uklabourmarket/february2026 |
| 2 | ONS developer hub | api | manual | Best source for adding structured UK macro time series once you promote the bridge beyond headline scraping. | https://developer.ons.gov.uk/ |
| 1 | Bank of Japan English what's new RSS | rss | yes | English-language BoJ updates are the easiest official feed for policy-sensitive yen developments. | https://www.boj.or.jp/en/rss/whatsnew.xml |
| 1 | Bank of Japan Japanese what's new RSS | rss | yes | Japanese feed can surface updates with less translation lag than the English site. | https://www.boj.or.jp/rss/whatsnew.xml |
| 1 | BoJ monetary policy releases 2026 page | html | yes | Direct BoJ policy statements are indispensable for yen direction and intervention risk framing. | https://www.boj.or.jp/en/mopo/mpmdeci/mpr_2026/index.htm |
| 2 | BoJ daily foreign exchange rates | html | yes | Provides official reference rates and a useful sanity check on recent yen moves. | https://www.boj.or.jp/en/statistics/market/forex/fxdaily/index.htm |
| 2 | Japan CPI release schedule | html | yes | Event calendar context helps the swarm weight imminent JPY volatility around CPI. | https://www.stat.go.jp/english/data/cpi/1582.htm |
| 2 | Japan labour force release schedule | html | yes | Useful for anticipating JPY-sensitive labor releases and avoiding stale economic context. | https://www.stat.go.jp/english/data/roudou/1543.html |
| 2 | e-Stat what's new | html | yes | Useful release monitor for new and updated Japanese official statistics. | https://www.e-stat.go.jp/en/whats-new |
| 2 | e-Stat API specification | api | manual | Structured Japanese macro data source once you add series-aware API adapters. | https://www.e-stat.go.jp/api/index.php/en/api-info/api-spec |
| 1 | Japan Ministry of Finance FX intervention monthly release | html | yes | Critical for GBPJPY because yen intervention risk can overwhelm otherwise clean rate-differential trades. | https://www.mof.go.jp/english/policy/international_policy/reference/feio/monthly/index.html |
| 2 | CFTC Commitments of Traders | html | yes | Leveraged-fund and asset-manager positioning is useful for spotting crowded GBP or JPY directional trades. | https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm |
