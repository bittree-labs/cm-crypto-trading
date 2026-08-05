package main

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"os/signal"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

// ============================================================
// Data Structures
// ============================================================

// PriceData holds latest price ticker from Binance Futures
type PriceData struct {
	Symbol string `json:"symbol"`
	Price  string `json:"price"`
	Time   int64  `json:"time"`
}

// BookTicker holds best bid/ask
type BookTicker struct {
	Symbol   string `json:"symbol"`
	BidPrice string `json:"bidPrice"`
	BidQty   string `json:"bidQty"`
	AskPrice string `json:"askPrice"`
	AskQty   string `json:"askQty"`
}

// OrderBook holds full depth snapshot
type OrderBook struct {
	LastUpdateID int64      `json:"lastUpdateId"`
	Bids         [][]string `json:"bids"`
	Asks         [][]string `json:"asks"`
}

// FundingRate holds latest funding rate
type FundingRate struct {
	Symbol      string `json:"symbol"`
	FundingTime int64  `json:"fundingTime"`
	FundingRate string `json:"fundingRate"`
	MarkPrice   string `json:"markPrice"`
}

// OpenInterest holds OI data
type OpenInterest struct {
	Symbol       string `json:"symbol"`
	OpenInterest string `json:"openInterest"`
	Time         int64  `json:"time"`
}

// MarketSnapshot is the consolidated state at one point in time
type MarketSnapshot struct {
	Timestamp    time.Time
	MarkPrice    float64
	BidPrice     float64
	AskPrice     float64
	BidQty       float64
	AskQty       float64
	Spread       float64
	SpreadPct    float64
	BidDepth5    float64 // sum of top 5 bid qtys
	AskDepth5    float64 // sum of top 5 ask qtys
	FundingRate  float64
	OpenInterest float64
	// Best 5 levels
	Bids [5]Level
	Asks [5]Level
}

// Level is one order book level
type Level struct {
	Price float64
	Qty   float64
}

// SignalResult is the output of one signal computation
type SignalResult struct {
	Name   string
	Value  float64 // -1.0 to +1.0
	Label  string  // human-readable judgment
	Weight float64
}

// RollingStats holds rolling window calculations
type RollingStats struct {
	mu          sync.Mutex
	Prices      []float64
	Timestamps  []time.Time
	MaxSize     int
	PrevOIPrice float64 // previous snapshot for OI delta
	PrevOI      float64
}

// ============================================================
// ANSI Colors
// ============================================================

const (
	reset  = "\033[0m"
	bold   = "\033[1m"
	dim    = "\033[2m"
	red    = "\033[31m"
	green  = "\033[32m"
	yellow = "\033[33m"
	blue   = "\033[34m"
	magenta = "\033[35m"
	cyan   = "\033[36m"
	white  = "\033[37m"
	bgRed  = "\033[41m"
	bgGreen = "\033[42m"
	bgYellow = "\033[43m"
	bgBlue  = "\033[44m"
)

// ============================================================
// API Client
// ============================================================

const (
	baseURL     = "https://fapi.binance.com"
	symbol      = "BTCUSDT"
	httpTimeout = 10 * time.Second
)

var client = &http.Client{Timeout: httpTimeout}

func fetchJSON(url string, target interface{}) error {
	resp, err := client.Get(url)
	if err != nil {
		return fmt.Errorf("GET %s: %w", url, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("status %d: %s", resp.StatusCode, string(body))
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("read body: %w", err)
	}

	if err := json.Unmarshal(body, target); err != nil {
		return fmt.Errorf("unmarshal: %w (body: %s)", err, string(body[:min(200, len(body))]))
	}
	return nil
}

func fetchPrice() (PriceData, error) {
	var p PriceData
	err := fetchJSON(baseURL+"/fapi/v1/ticker/price?symbol="+symbol, &p)
	return p, err
}

func fetchBookTicker() (BookTicker, error) {
	var bt BookTicker
	err := fetchJSON(baseURL+"/fapi/v1/ticker/bookTicker?symbol="+symbol, &bt)
	return bt, err
}

func fetchDepth(limit int) (OrderBook, error) {
	var ob OrderBook
	err := fetchJSON(fmt.Sprintf("%s/fapi/v1/depth?symbol=%s&limit=%d", baseURL, symbol, limit), &ob)
	return ob, err
}

func fetchFundingRate() (FundingRate, error) {
	var fr []FundingRate
	err := fetchJSON(baseURL+"/fapi/v1/fundingRate?symbol="+symbol+"&limit=1", &fr)
	if err != nil {
		return FundingRate{}, err
	}
	if len(fr) == 0 {
		return FundingRate{}, fmt.Errorf("empty funding rate response")
	}
	return fr[0], nil
}

func fetchOpenInterest() (OpenInterest, error) {
	var oi OpenInterest
	err := fetchJSON(baseURL+"/fapi/v1/openInterest?symbol="+symbol, &oi)
	return oi, err
}

// ============================================================
// Market Snapshot Collector (runs every second)
// ============================================================

type SnapshotCollector struct {
	mu      sync.RWMutex
	Latest  MarketSnapshot
	History []MarketSnapshot
	MaxHist int
}

func NewSnapshotCollector() *SnapshotCollector {
	return &SnapshotCollector{
		MaxHist: 300, // keep 5 minutes of data
		History: make([]MarketSnapshot, 0, 300),
	}
}

func (sc *SnapshotCollector) collect() {
	depth, err := fetchDepth(5)
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s[depth err]%s %v\n", red, reset, err)
		return
	}

	bt, err := fetchBookTicker()
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s[book err]%s %v\n", red, reset, err)
		return
	}

	snap := MarketSnapshot{Timestamp: time.Now()}

	// Preserve previous derivatives data (funding rate, OI)
	sc.mu.RLock()
	snap.FundingRate = sc.Latest.FundingRate
	snap.OpenInterest = sc.Latest.OpenInterest
	sc.mu.RUnlock()

	// Parse best bid/ask
	snap.BidPrice, _ = strconv.ParseFloat(bt.BidPrice, 64)
	snap.AskPrice, _ = strconv.ParseFloat(bt.AskPrice, 64)
	snap.BidQty, _ = strconv.ParseFloat(bt.BidQty, 64)
	snap.AskQty, _ = strconv.ParseFloat(bt.AskQty, 64)

	if snap.AskPrice > 0 && snap.BidPrice > 0 {
		snap.Spread = snap.AskPrice - snap.BidPrice
		snap.SpreadPct = (snap.Spread / snap.BidPrice) * 100
	}
	snap.MarkPrice = (snap.BidPrice + snap.AskPrice) / 2

	// Parse depth levels
	for i := 0; i < 5 && i < len(depth.Bids); i++ {
		snap.Bids[i].Price, _ = strconv.ParseFloat(depth.Bids[i][0], 64)
		snap.Bids[i].Qty, _ = strconv.ParseFloat(depth.Bids[i][1], 64)
		snap.BidDepth5 += snap.Bids[i].Qty
	}
	for i := 0; i < 5 && i < len(depth.Asks); i++ {
		snap.Asks[i].Price, _ = strconv.ParseFloat(depth.Asks[i][0], 64)
		snap.Asks[i].Qty, _ = strconv.ParseFloat(depth.Asks[i][1], 64)
		snap.AskDepth5 += snap.Asks[i].Qty
	}

	sc.mu.Lock()
	sc.Latest = snap
	sc.History = append(sc.History, snap)
	if len(sc.History) > sc.MaxHist {
		sc.History = sc.History[len(sc.History)-sc.MaxHist:]
	}
	sc.mu.Unlock()
}

func (sc *SnapshotCollector) collectDerivatives() {
	// Funding rate
	fr, err := fetchFundingRate()
	if err == nil {
		sc.mu.Lock()
		sc.Latest.FundingRate, _ = strconv.ParseFloat(fr.FundingRate, 64)
		sc.mu.Unlock()
	}

	// Open interest
	oi, err := fetchOpenInterest()
	if err == nil {
		sc.mu.Lock()
		sc.Latest.OpenInterest, _ = strconv.ParseFloat(oi.OpenInterest, 64)
		sc.mu.Unlock()
	}
}

// ============================================================
// Signal Engine
// ============================================================

type SignalEngine struct {
	collector    *SnapshotCollector
	rolling      *RollingStats
	prevSpread   float64
	prevSpreadTs time.Time
}

func NewSignalEngine(sc *SnapshotCollector) *SignalEngine {
	return &SignalEngine{
		collector: sc,
		rolling: &RollingStats{
			MaxSize: 60, // 60 seconds rolling window
			Prices:  make([]float64, 0, 60),
		},
	}
}

// L1: Order Book Imbalance (-1 to +1)
// >0 = bid heavy (bullish), <0 = ask heavy (bearish)
func (se *SignalEngine) signalOrderBookImbalance() SignalResult {
	se.collector.mu.RLock()
	snap := se.collector.Latest
	se.collector.mu.RUnlock()

	total := snap.BidDepth5 + snap.AskDepth5
	if total == 0 {
		return SignalResult{Name: "订单簿失衡", Value: 0, Label: "无数据", Weight: 0.20}
	}

	imbalance := (snap.BidDepth5 - snap.AskDepth5) / total // -1 to +1

	var label string
	switch {
	case imbalance > 0.3:
		label = "买盘强势 🔥"
	case imbalance > 0.15:
		label = "买盘偏强"
	case imbalance < -0.3:
		label = "卖盘强势 ❄️"
	case imbalance < -0.15:
		label = "卖盘偏强"
	default:
		label = "均衡"
	}

	return SignalResult{Name: "订单簿失衡", Value: imbalance, Label: label, Weight: 0.20}
}

// L2: Spread Anomaly (0 to +1, higher = more abnormal)
func (se *SignalEngine) signalSpreadAnomaly() SignalResult {
	se.collector.mu.RLock()
	snap := se.collector.Latest
	se.collector.mu.RUnlock()

	// BTCUSDT typically has 0.1-0.5 USD spread (0.0002% - 0.001%)
	// Anything above 2 USD or 0.005% is abnormal
	var score float64
	var label string

	spreadUSD := snap.Spread
	spreadPct := snap.SpreadPct

	switch {
	case spreadPct > 0.01: // extreme
		score = 1.0
		label = "价差极大 ⚠️"
	case spreadPct > 0.005:
		score = 0.7
		label = "价差扩大"
	case spreadPct > 0.002:
		score = 0.3
		label = "价差略大"
	default:
		score = 0
		label = "正常"
	}

	_ = spreadUSD // keep for future use

	// Track sudden spread widening
	if se.prevSpread > 0 && snap.Spread > se.prevSpread*2 {
		score = math.Max(score, 0.8)
		label = "价差突扩 🚨"
	}
	se.prevSpread = snap.Spread

	return SignalResult{Name: "价差异常", Value: score, Label: label, Weight: 0.10}
}

// L3: Whale Wall Detection (-1 to +1)
func (se *SignalEngine) signalWhaleWall() SignalResult {
	se.collector.mu.RLock()
	snap := se.collector.Latest
	se.collector.mu.RUnlock()

	var maxBidQty, maxAskQty float64
	for _, b := range snap.Bids {
		if b.Qty > maxBidQty {
			maxBidQty = b.Qty
		}
	}
	for _, a := range snap.Asks {
		if a.Qty > maxAskQty {
			maxAskQty = a.Qty
		}
	}

	const whaleThreshold = 30.0 // BTC

	var score float64
	var label string

	bidWhale := maxBidQty > whaleThreshold
	askWhale := maxAskQty > whaleThreshold

	switch {
	case bidWhale && askWhale:
		score = 0
		label = "多空对峙 🐳"
	case bidWhale:
		score = 0.5
		label = fmt.Sprintf("鲸鱼买墙 %.0fBTC 🛡️", maxBidQty)
	case askWhale:
		score = -0.5
		label = fmt.Sprintf("鲸鱼卖墙 %.0fBTC ⚔️", maxAskQty)
	default:
		score = 0
		label = "无大单"
	}

	return SignalResult{Name: "大单检测", Value: score, Label: label, Weight: 0.15}
}

// L4: Micro Trend Momentum (-1 to +1)
func (se *SignalEngine) signalMicroTrend() SignalResult {
	se.collector.mu.RLock()
	snap := se.collector.Latest
	history := make([]MarketSnapshot, len(se.collector.History))
	copy(history, se.collector.History)
	se.collector.mu.RUnlock()

	se.rolling.mu.Lock()
	se.rolling.Prices = append(se.rolling.Prices, snap.MarkPrice)
	se.rolling.Timestamps = append(se.rolling.Timestamps, snap.Timestamp)
	if len(se.rolling.Prices) > se.rolling.MaxSize {
		se.rolling.Prices = se.rolling.Prices[len(se.rolling.Prices)-se.rolling.MaxSize:]
		se.rolling.Timestamps = se.rolling.Timestamps[len(se.rolling.Timestamps)-se.rolling.MaxSize:]
	}
	prices := make([]float64, len(se.rolling.Prices))
	copy(prices, se.rolling.Prices)
	se.rolling.mu.Unlock()

	if len(prices) < 5 {
		return SignalResult{Name: "微趋势", Value: 0, Label: "采集中...", Weight: 0.20}
	}

	// Short-term: last 10 seconds
	n10 := min(10, len(prices))
	shortChange := (prices[len(prices)-1] - prices[len(prices)-n10]) / prices[len(prices)-n10] * 100

	// Medium-term: last 30 seconds
	n30 := min(30, len(prices))
	medChange := (prices[len(prices)-1] - prices[len(prices)-n30]) / prices[len(prices)-n30] * 100

	// Acceleration: short vs medium
	accel := shortChange - medChange

	var score, momentumScore float64
	momentumScore = medChange * 100 // scale small pct to meaningful range

	// Clamp momentum
	if momentumScore > 1 {
		momentumScore = 1
	} else if momentumScore < -1 {
		momentumScore = -1
	}

	// Add acceleration component
	score = momentumScore + accel*50 // acceleration gets extra weight
	if score > 1 {
		score = 1
	} else if score < -1 {
		score = -1
	}

	var label string
	switch {
	case score > 0.5:
		label = fmt.Sprintf("强上涨 %.4f%% 📈", medChange)
	case score > 0.2:
		label = fmt.Sprintf("温和上涨 %.4f%% ↗️", medChange)
	case score < -0.5:
		label = fmt.Sprintf("强下跌 %.4f%% 📉", medChange)
	case score < -0.2:
		label = fmt.Sprintf("温和下跌 %.4f%% ↘️", medChange)
	default:
		label = "横盘震荡 ➡️"
	}

	_ = history
	return SignalResult{Name: "微趋势", Value: score, Label: label, Weight: 0.20}
}

// L5: Funding Rate Extremes (-1 to +1)
func (se *SignalEngine) signalFundingRate() SignalResult {
	se.collector.mu.RLock()
	fr := se.collector.Latest.FundingRate
	se.collector.mu.RUnlock()

	// funding rate is per 8h, annualized = fundingRate * 3 * 365
	annualized := fr * 3 * 365 * 100 // as percentage

	var score float64
	var label string

	switch {
	case annualized > 50: // extremely bullish crowded
		score = -0.8
		label = fmt.Sprintf("多头极度拥挤 %.1f%% 年化 ⛔", annualized)
	case annualized > 20:
		score = -0.4
		label = fmt.Sprintf("多头偏拥挤 %.1f%% 年化", annualized)
	case annualized < -20:
		score = 0.8
		label = fmt.Sprintf("空头极度拥挤 %.1f%% 年化 ⛔", annualized)
	case annualized < -5:
		score = 0.4
		label = fmt.Sprintf("空头偏拥挤 %.1f%% 年化", annualized)
	case annualized > 5:
		score = -0.1
		label = fmt.Sprintf("偏多 %.1f%% 年化", annualized)
	default:
		score = 0
		label = fmt.Sprintf("中性 %.1f%% 年化", annualized)
	}

	return SignalResult{Name: "资金费率", Value: score, Label: label, Weight: 0.10}
}

// L6: OI-Price Divergence (-1 to +1)
// Bullish: price up + OI up (new longs entering)
// Bearish: price down + OI up (new shorts entering)
// Warning bull: price up + OI down (shorts covering, may not sustain)
// Warning bear: price down + OI down (longs exiting, may be near bottom)
func (se *SignalEngine) signalOIDivergence() SignalResult {
	se.collector.mu.RLock()
	snap := se.collector.Latest
	se.collector.mu.RUnlock()

	se.rolling.mu.Lock()
	prevPrice := se.rolling.PrevOIPrice
	prevOI := se.rolling.PrevOI
	se.rolling.PrevOIPrice = snap.MarkPrice
	se.rolling.PrevOI = snap.OpenInterest
	se.rolling.mu.Unlock()

	if prevPrice == 0 || prevOI == 0 {
		return SignalResult{Name: "OI背离", Value: 0, Label: "采集中...", Weight: 0.15}
	}

	priceDelta := (snap.MarkPrice - prevPrice) / prevPrice * 100
	oiDelta := (snap.OpenInterest - prevOI) / prevOI * 100

	var score float64
	var label string

	switch {
	case priceDelta > 0.05 && oiDelta > 0.5:
		score = 0.6
		label = "价量齐升（多头建仓）🟢"
	case priceDelta > 0.05 && oiDelta < -0.5:
		score = 0.15
		label = "价升量缩（空头平仓推动）🟡"
	case priceDelta < -0.05 && oiDelta > 0.5:
		score = -0.6
		label = "价跌量增（空头建仓）🔴"
	case priceDelta < -0.05 && oiDelta < -0.5:
		score = -0.15
		label = "价跌量缩（多头平仓）🟡"
	case priceDelta > 0.02:
		score = 0.2
		label = fmt.Sprintf("微涨 %.2f%%", priceDelta)
	case priceDelta < -0.02:
		score = -0.2
		label = fmt.Sprintf("微跌 %.2f%%", priceDelta)
	default:
		score = 0
		label = "平稳"
	}

	return SignalResult{Name: "OI背离", Value: score, Label: label, Weight: 0.15}
}

// ============================================================
// Signal Fusion
// ============================================================

func fuseSignals(signals []SignalResult) (float64, string, float64) {
	var weightedSum, totalWeight float64
	for _, s := range signals {
		weightedSum += s.Value * s.Weight
		totalWeight += s.Weight
	}

	if totalWeight == 0 {
		return 0, "无信号", 0
	}

	composite := weightedSum / totalWeight

	var regime string
	switch {
	case composite > 0.5:
		regime = "强势看多 🟢🟢"
	case composite > 0.2:
		regime = "温和看多 🟢"
	case composite < -0.5:
		regime = "强势看空 🔴🔴"
	case composite < -0.2:
		regime = "温和看空 🔴"
	default:
		regime = "中性/震荡 ⚪"
	}

	// Confidence: how strongly signals agree
	var varianceSum float64
	for _, s := range signals {
		diff := s.Value - composite
		varianceSum += diff * diff
	}
	confidence := 1.0 - math.Sqrt(varianceSum/float64(len(signals)))
	if confidence < 0 {
		confidence = 0
	}
	if confidence > 1 {
		confidence = 1
	}

	return composite, regime, confidence
}

// ============================================================
// Terminal UI
// ============================================================

func clearScreen() {
	fmt.Print("\033[2J\033[H")
}

func moveTo(row, col int) {
	fmt.Printf("\033[%d;%dH", row, col)
}

func colorBar(value float64, width int) string {
	// value from -1 (red) to +1 (green), 0 = neutral
	var bar strings.Builder
	half := width / 2

	if value >= 0 {
		greenLen := int(math.Round(value * float64(half)))
		neutralLen := half - greenLen
		for i := 0; i < half; i++ {
			bar.WriteString(dim + "─" + reset)
		}
		bar.WriteString("│")
		for i := 0; i < greenLen; i++ {
			bar.WriteString(green + "█" + reset)
		}
		for i := 0; i < neutralLen; i++ {
			bar.WriteString(dim + "░" + reset)
		}
	} else {
		redLen := int(math.Round(-value * float64(half)))
		neutralLen := half - redLen
		for i := 0; i < neutralLen; i++ {
			bar.WriteString(dim + "░" + reset)
		}
		for i := 0; i < redLen; i++ {
			bar.WriteString(red + "█" + reset)
		}
		bar.WriteString("│")
		for i := 0; i < half; i++ {
			bar.WriteString(dim + "─" + reset)
		}
	}
	return bar.String()
}

func render(snap MarketSnapshot, signals []SignalResult, composite float64, regime string, confidence float64, uptime time.Duration) {
	clearScreen()

	// Header
	fmt.Printf("%s%s╔══════════════════════════════════════════════════════════════╗%s\n", bold, cyan, reset)
	fmt.Printf("%s║%s  %sBTCUSDT 永续合约 — 价格发现引擎%s                          %s║%s\n", cyan, reset, bold, reset, cyan, reset)
	fmt.Printf("%s╚══════════════════════════════════════════════════════════════╝%s\n", cyan, reset)
	fmt.Println()

	// Price line
	priceColor := white
	if composite > 0.2 {
		priceColor = green
	} else if composite < -0.2 {
		priceColor = red
	}
	fmt.Printf("  %s标的价格  %s%s$%.2f%s", dim, bold, priceColor, snap.MarkPrice, reset)
	fmt.Printf("    %sBid %s$%.2f%s", dim, green, snap.BidPrice, reset)
	fmt.Printf("    %sAsk %s$%.2f%s", dim, red, snap.AskPrice, reset)
	fmt.Printf("    %sSpread %s$%.2f (%.4f%%)%s\n", dim, yellow, snap.Spread, snap.SpreadPct, reset)

	// Depth summary
	fmt.Printf("  %s5档深度  %sBid: %s%.1f BTC%s  │  %sAsk: %s%.1f BTC%s", dim, dim, green, snap.BidDepth5, reset, dim, red, snap.AskDepth5, reset)
	ratio := snap.BidDepth5 / (snap.BidDepth5 + snap.AskDepth5) * 100
	fmt.Printf("    %sBid占比 %s%.0f%%%s\n", dim, cyan, ratio, reset)

	// Funding & OI
	frAnnual := snap.FundingRate * 3 * 365 * 100
	fmt.Printf("  %s资金费率  %s%.6f%%%s", dim, yellow, snap.FundingRate*100, reset)
	fmt.Printf("    %s年化 %s%.2f%%%s", dim, yellow, frAnnual, reset)
	fmt.Printf("    %sOI %s%.0f BTC%s", dim, magenta, snap.OpenInterest, reset)
	fmt.Printf("    %s运行 %s%v%s\n", dim, white, uptime.Round(time.Second), reset)
	fmt.Println()

	// Composite score
	fmt.Printf("  %s═══════ 综合情绪 ═══════%s\n", bold, reset)
	scoreColor := white
	switch {
	case composite > 0.5:
		scoreColor = green + bold
	case composite > 0.2:
		scoreColor = green
	case composite < -0.5:
		scoreColor = red + bold
	case composite < -0.2:
		scoreColor = red
	}
	fmt.Printf("  %s综合指数 %s%+.3f%s  %s%s%s  ", dim, scoreColor, composite, reset, bold, regime, reset)
	fmt.Printf("%s置信度 %.0f%%%s\n", dim, confidence*100, reset)
	fmt.Printf("  %s\n", colorBar(composite, 50))
	fmt.Println()

	// Signal details
	fmt.Printf("  %s═══════ 信号明细 ═══════%s\n", bold, reset)
	fmt.Printf("  %-16s %8s %8s   %s\n", "信号", "权重", "数值", "判断")
	fmt.Printf("  %s\n", strings.Repeat("─", 65))

	for _, s := range signals {
		vColor := white
		switch {
		case s.Value > 0.3:
			vColor = green
		case s.Value > 0.1:
			vColor = green + dim
		case s.Value < -0.3:
			vColor = red
		case s.Value < -0.1:
			vColor = red + dim
		}
		bar := miniBar(s.Value)
		fmt.Printf("  %-16s %s%4.0f%%%s  %s%+6.3f%s %s %s\n",
			s.Name, dim, s.Weight*100, reset, vColor, s.Value, reset, bar, s.Label)
	}
	fmt.Println()

	// Order book visualization
	fmt.Printf("  %s═══════ 订单簿 %s(5档)%s ═══════%s\n", bold, dim, reset, reset)
	fmt.Printf("  %s%-12s %10s  %10s %-12s%s\n", dim, "卖价", "数量(BTC)", "数量(BTC)", "买价", reset)

	for i := 4; i >= 0; i-- {
		ask := snap.Asks[i]
		bid := snap.Bids[i]
		askBar := strings.Repeat("█", int(math.Min(ask.Qty/2, 20)))
		bidBar := strings.Repeat("█", int(math.Min(bid.Qty/2, 20)))
		fmt.Printf("  %s$%-10.2f%s %s%10.3f%s %s  %s%10.3f%s %s$%-10.2f%s\n",
			red, ask.Price, reset,
			red, ask.Qty, reset,
			red+dim+askBar+reset,
			green+dim+bidBar+reset,
			bid.Qty, reset,
			green, bid.Price, reset)
	}
	fmt.Println()

	// Legend
	fmt.Printf("  %sCtrl+C 退出  │  刷新频率: 1s (价格/深度) + 60s (费率/OI)  │  %s%s\n",
		dim, time.Now().Format("15:04:05"), reset)
}

func miniBar(value float64) string {
	if value > 0.05 {
		return green + "▲" + reset
	} else if value < -0.05 {
		return red + "▼" + reset
	}
	return dim + "─" + reset
}

// ============================================================
// Logging
// ============================================================

type EventLogger struct {
	mu   sync.Mutex
	file *os.File
}

func NewEventLogger(path string) (*EventLogger, error) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		return nil, err
	}
	return &EventLogger{file: f}, nil
}

func (el *EventLogger) Log(composite float64, regime string, signals []SignalResult, snap MarketSnapshot) {
	if el == nil || el.file == nil {
		return
	}
	if math.Abs(composite) < 0.3 {
		return // only log significant signals
	}

	el.mu.Lock()
	defer el.mu.Unlock()

	ts := snap.Timestamp.Format("2006-01-02 15:04:05")
	fmt.Fprintf(el.file, "[%s] composite=%.3f regime=%s price=%.2f\n", ts, composite, regime, snap.MarkPrice)
	for _, s := range signals {
		if math.Abs(s.Value) > 0.15 {
			fmt.Fprintf(el.file, "  %s: %.3f (%s)\n", s.Name, s.Value, s.Label)
		}
	}
	fmt.Fprintln(el.file)
}

func (el *EventLogger) Close() {
	if el != nil && el.file != nil {
		el.file.Close()
	}
}

// ============================================================
// Main
// ============================================================

func main() {
	fmt.Println("🚀 BTCUSDT 价格发现引擎启动中...")

	// Create log directory
	logPath := "events.log"

	collector := NewSnapshotCollector()
	engine := NewSignalEngine(collector)

	logger, err := NewEventLogger(logPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "日志初始化失败: %v (继续运行)\n", err)
	}

	// Initial data fetch
	fmt.Print("  初始化数据...")
	collector.collect()
	collector.collectDerivatives()
	fmt.Println(" ✓")

	// Graceful shutdown
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

	// Ticker for fast loop (1s)
	fastTicker := time.NewTicker(1 * time.Second)
	defer fastTicker.Stop()

	// Ticker for slow loop (60s derivatives)
	slowTicker := time.NewTicker(60 * time.Second)
	defer slowTicker.Stop()

	startTime := time.Now()
	slowCount := 0

	// Immediate first render
	runCycle(collector, engine, logger, startTime)

	for {
		select {
		case <-sigCh:
			fmt.Printf("\n%s\n", reset)
			fmt.Println("👋 价格发现引擎已停止")
			logger.Close()
			fmt.Printf("事件日志已保存至: %s\n", logPath)
			return

		case <-fastTicker.C:
			collector.collect()
			slowCount++
			if slowCount >= 60 {
				collector.collectDerivatives()
				slowCount = 0
			}
			runCycle(collector, engine, logger, startTime)

		case <-slowTicker.C:
			collector.collectDerivatives()
		}
	}
}

func runCycle(collector *SnapshotCollector, engine *SignalEngine, logger *EventLogger, startTime time.Time) {
	// Compute all signals
	signals := []SignalResult{
		engine.signalOrderBookImbalance(),
		engine.signalMicroTrend(),
		engine.signalWhaleWall(),
		engine.signalOIDivergence(),
		engine.signalFundingRate(),
		engine.signalSpreadAnomaly(),
	}

	// Sort by absolute value desc
	sort.Slice(signals, func(i, j int) bool {
		return math.Abs(signals[i].Value) > math.Abs(signals[j].Value)
	})

	composite, regime, confidence := fuseSignals(signals)

	collector.mu.RLock()
	snap := collector.Latest
	collector.mu.RUnlock()

	render(snap, signals, composite, regime, confidence, time.Since(startTime))
	logger.Log(composite, regime, signals, snap)
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
