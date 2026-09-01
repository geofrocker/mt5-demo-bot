//+------------------------------------------------------------------+
//| ConservativeTrendEA.mq5                                          |
//| Demo-only trend EA with hard daily loss and position caps.       |
//| Not financial advice. No martingale, no grid, no averaging.      |
//+------------------------------------------------------------------+
#property copyright "mt5-demo-bot"
#property version   "1.00"
#property description "Conservative demo EA: EMA trend + RSI pullback, 0.5% risk, daily loss cap."

#include <Trade/Trade.mqh>

input group "=== Risk (keep these conservative on demo) ==="
input double RiskPercent          = 0.5;    // % of equity risked per trade
input double DailyLossPercent     = 2.0;    // stop trading if day is down this %
input int    MaxTradesPerDay      = 1;      // hard cap on new entries per day
input int    MaxOpenPositions     = 1;      // never stack positions
input int    MagicNumber          = 260901;

input group "=== Strategy ==="
input ENUM_TIMEFRAMES Timeframe   = PERIOD_H1;
input int    FastEMA              = 20;
input int    SlowEMA              = 50;
input int    TrendEMA             = 200;
input int    RSIPeriod            = 14;
input double RSIBuyMax            = 40.0;   // only buy on a pullback (RSI not hot)
input double RSISellMin           = 60.0;   // only sell on a bounce (RSI not washed out)
input int    ATRPeriod            = 14;
input double ATRStopMult          = 2.0;    // stop distance = this * ATR
input double RewardRatio          = 1.5;    // take-profit = this * stop distance
input int    SlippagePoints       = 20;
input int    MaxSpreadPoints      = 25;     // skip entry if spread is wider

input group "=== Session (server time) ==="
input bool   UseSessionFilter     = true;
input int    SessionStartHour     = 7;      // roughly London open
input int    SessionEndHour       = 20;     // skip thin late session

CTrade         trade;
int            fastHandle = INVALID_HANDLE;
int            slowHandle = INVALID_HANDLE;
int            trendHandle = INVALID_HANDLE;
int            rsiHandle  = INVALID_HANDLE;
int            atrHandle  = INVALID_HANDLE;
datetime       lastBarTime = 0;
datetime       dayStamp   = 0;
double         dayStartEquity = 0.0;
int            tradesToday = 0;
bool           haltedToday = false;

int OnInit()
  {
   if(FastEMA >= SlowEMA || SlowEMA >= TrendEMA)
     {
      Print("EMA periods must be Fast < Slow < Trend.");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(RiskPercent <= 0.0 || RiskPercent > 2.0)
     {
      Print("RiskPercent must be in (0, 2].");
      return INIT_PARAMETERS_INCORRECT;
     }

   fastHandle  = iMA(_Symbol, Timeframe, FastEMA, 0, MODE_EMA, PRICE_CLOSE);
   slowHandle  = iMA(_Symbol, Timeframe, SlowEMA, 0, MODE_EMA, PRICE_CLOSE);
   trendHandle = iMA(_Symbol, Timeframe, TrendEMA, 0, MODE_EMA, PRICE_CLOSE);
   rsiHandle   = iRSI(_Symbol, Timeframe, RSIPeriod, PRICE_CLOSE);
   atrHandle   = iATR(_Symbol, Timeframe, ATRPeriod);

   if(fastHandle == INVALID_HANDLE || slowHandle == INVALID_HANDLE ||
      trendHandle == INVALID_HANDLE || rsiHandle == INVALID_HANDLE ||
      atrHandle == INVALID_HANDLE)
     {
      Print("Failed to create indicator handles.");
      return INIT_FAILED;
     }

   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(SlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);
   ResetDailyCounters();
   Print("ConservativeTrendEA initialized on ", _Symbol, " ", EnumToString(Timeframe));
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   if(fastHandle  != INVALID_HANDLE) IndicatorRelease(fastHandle);
   if(slowHandle  != INVALID_HANDLE) IndicatorRelease(slowHandle);
   if(trendHandle != INVALID_HANDLE) IndicatorRelease(trendHandle);
   if(rsiHandle   != INVALID_HANDLE) IndicatorRelease(rsiHandle);
   if(atrHandle   != INVALID_HANDLE) IndicatorRelease(atrHandle);
  }

void OnTick()
  {
   ResetDailyCounters();

   if(!MQLInfoInteger(MQL_TRADE_ALLOWED) || !TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      return;

   if(HaltedByDailyLoss())
      return;

   if(!IsNewBar())
      return;

   if(CountOurPositions() >= MaxOpenPositions)
      return;

   if(tradesToday >= MaxTradesPerDay)
      return;

   if(UseSessionFilter && !InSession())
      return;

   if(SpreadPoints() > MaxSpreadPoints)
     {
      Print("Spread too wide: ", SpreadPoints(), " points");
      return;
     }

   double fast[], slow[], trend[], rsi[], atr[];
   ArraySetAsSeries(fast, true);
   ArraySetAsSeries(slow, true);
   ArraySetAsSeries(trend, true);
   ArraySetAsSeries(rsi, true);
   ArraySetAsSeries(atr, true);
   if(CopyBuffer(fastHandle, 0, 0, 4, fast)  < 4) return;
   if(CopyBuffer(slowHandle, 0, 0, 4, slow)  < 4) return;
   if(CopyBuffer(trendHandle, 0, 0, 4, trend) < 4) return;
   if(CopyBuffer(rsiHandle,  0, 0, 4, rsi)   < 4) return;
   if(CopyBuffer(atrHandle,  0, 0, 3, atr)   < 3) return;

   // [1] = last closed bar (we only run on a new bar). [2] = bar before that.
   const double fastNow  = fast[1];
   const double slowNow  = slow[1];
   const double trendNow = trend[1];
   const double rsiNow   = rsi[1];
   const double rsiPrev  = rsi[2];
   const double atrNow   = atr[1];

   if(atrNow <= 0.0)
      return;

   const bool uptrend   = (fastNow > slowNow && slowNow > trendNow);
   const bool downtrend = (fastNow < slowNow && slowNow < trendNow);

   // Enter only after RSI turns back with the trend from a pullback.
   const bool longSignal  = uptrend && rsiPrev < RSIBuyMax && rsiNow > rsiPrev && rsiNow < 55.0;
   const bool shortSignal = downtrend && rsiPrev > RSISellMin && rsiNow < rsiPrev && rsiNow > 45.0;

   if(longSignal)
      OpenTrade(ORDER_TYPE_BUY, atrNow);
   else if(shortSignal)
      OpenTrade(ORDER_TYPE_SELL, atrNow);
  }

bool IsNewBar()
  {
   datetime t[1];
   if(CopyTime(_Symbol, Timeframe, 0, 1, t) != 1)
      return false;
   if(t[0] == lastBarTime)
      return false;
   lastBarTime = t[0];
   return true;
  }

void ResetDailyCounters()
  {
   MqlDateTime now;
   TimeToStruct(TimeCurrent(), now);
   datetime today = StringToTime(StringFormat("%04d.%02d.%02d", now.year, now.mon, now.day));
   if(today != dayStamp)
     {
      dayStamp = today;
      dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
      tradesToday = 0;
      haltedToday = false;
      Print("New trading day. Start equity=", DoubleToString(dayStartEquity, 2));
     }
  }

bool HaltedByDailyLoss()
  {
   if(haltedToday)
      return true;
   if(dayStartEquity <= 0.0)
      return false;
   const double dd = (dayStartEquity - AccountInfoDouble(ACCOUNT_EQUITY)) / dayStartEquity * 100.0;
   if(dd >= DailyLossPercent)
     {
      haltedToday = true;
      Print("Daily loss cap hit (", DoubleToString(dd, 2), "%). No more trades today.");
      return true;
     }
   return false;
  }

bool InSession()
  {
   MqlDateTime now;
   TimeToStruct(TimeCurrent(), now);
   if(now.day_of_week == 0 || now.day_of_week == 6)
      return false;
   return (now.hour >= SessionStartHour && now.hour < SessionEndHour);
  }

int SpreadPoints()
  {
   const double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(point <= 0.0)
      return INT_MAX;
   const double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   const double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   return (int)MathRound((ask - bid) / point);
  }

int CountOurPositions()
  {
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;
      count++;
     }
   return count;
  }

void OpenTrade(const ENUM_ORDER_TYPE type, const double atr)
  {
   const double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   const double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   const double price = (type == ORDER_TYPE_BUY) ? ask : bid;
   const double slDistance = atr * ATRStopMult;
   const double tpDistance = slDistance * RewardRatio;

   double sl, tp;
   if(type == ORDER_TYPE_BUY)
     {
      sl = price - slDistance;
      tp = price + tpDistance;
     }
   else
     {
      sl = price + slDistance;
      tp = price - tpDistance;
     }

   sl = NormalizePrice(sl);
   tp = NormalizePrice(tp);

   const double lots = VolumeForRisk(price, sl);
   if(lots <= 0.0)
     {
      Print("Lot size came out 0 — check stops / symbol volume limits.");
      return;
     }

   trade.SetExpertMagicNumber(MagicNumber);
   const bool ok = (type == ORDER_TYPE_BUY)
                   ? trade.Buy(lots, _Symbol, price, sl, tp, "ConservativeTrendEA")
                   : trade.Sell(lots, _Symbol, price, sl, tp, "ConservativeTrendEA");

   if(ok)
     {
      tradesToday++;
      Print("Opened ", EnumToString(type), " lots=", DoubleToString(lots, 2),
            " sl=", DoubleToString(sl, _Digits), " tp=", DoubleToString(tp, _Digits));
     }
   else
      Print("Order failed: ", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
  }

double NormalizePrice(const double price)
  {
   const double tick = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tick <= 0.0)
      return NormalizeDouble(price, _Digits);
   return NormalizeDouble(MathRound(price / tick) * tick, _Digits);
  }

double VolumeForRisk(const double entry, const double sl)
  {
   const double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   const double riskMoney = equity * (RiskPercent / 100.0);
   const double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   const double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   const double lossDistance = MathAbs(entry - sl);

   if(tickSize <= 0.0 || tickValue <= 0.0 || lossDistance <= 0.0)
      return 0.0;

   const double ticks = lossDistance / tickSize;
   const double rawLots = riskMoney / (ticks * tickValue);

   const double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   const double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   const double step    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0.0)
      return 0.0;

   double lots = MathFloor(rawLots / step) * step;
   lots = MathMax(minLot, MathMin(maxLot, lots));
   const int volDigits = (step >= 1.0) ? 0 : (int)MathCeil(-MathLog10(step));
   lots = NormalizeDouble(lots, volDigits);

   // If even min lot risks more than 1.5x the intended amount, skip.
   const double minLotRisk = (minLot * ticks * tickValue);
   if(minLotRisk > riskMoney * 1.5)
     {
      Print("Min lot risk ", DoubleToString(minLotRisk, 2),
            " exceeds allowance ", DoubleToString(riskMoney, 2), " — skip.");
      return 0.0;
     }
   return lots;
  }
