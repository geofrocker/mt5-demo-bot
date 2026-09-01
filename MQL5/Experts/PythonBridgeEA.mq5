//+------------------------------------------------------------------+
//| PythonBridgeEA.mq5                                               |
//| Executes commands from the local Python hook. Demo-only default. |
//| Not financial advice. No martingale / grid / averaging.          |
//+------------------------------------------------------------------+
#property copyright "mt5-demo-bot"
#property version   "1.05"
#property description "Python hook + Kaufman ER / EMA50 H4 trend. Per-symbol files. Demo only."

#include <Trade/Trade.mqh>

input group "=== Hook ==="
input string HookToken            = "demo-local-hook";
input string HookHost             = "127.0.0.1";
input int    HookPort             = 18789;
input bool   EnableTcp            = false;  // leave off when several charts are attached

input group "=== Safety ==="
input bool   RequireDemo          = true;
input double MaxVolume            = 0.10;
input int    MaxOpenPositions     = 1;      // this chart / symbol
input int    MaxAccountPositions  = 3;      // all hook-managed symbols
input double DailyLossPercent     = 2.0;
input int    MaxSpreadPoints      = 30;
input int    SlippagePoints       = 20;
input int    MagicNumber          = 260902;
input bool   AllowLive            = false;  // leave false

input group "=== Signal (Kaufman ER + EMA50 on H4) ==="
input ENUM_TIMEFRAMES SignalTF    = PERIOD_H4;
input int    ErPeriod             = 10;
input double ErMin                = 0.40;
input int    TrendEMA             = 50;
input int    ATRPeriod            = 14;
input double ATRStopMult          = 2.5;
input double RewardRatio          = 2.0;

CTrade   trade;
int      tcpSock = INVALID_HANDLE;
string   tcpRx = "";
uint     lastTcpTry = 0;
datetime dayStamp = 0;
double   dayStartEquity = 0.0;
bool     haltedToday = false;
bool     haltedByPython = false;
string   lastStatus = "starting";
string   lastResult = "";
int      trendHandle = INVALID_HANDLE;
int      atrHandle  = INVALID_HANDLE;

int OnInit()
  {
   if(RequireDemo && !AllowLive && AccountInfoInteger(ACCOUNT_TRADE_MODE) != ACCOUNT_TRADE_MODE_DEMO)
     {
      lastStatus = "refused: not a demo account";
      Print(lastStatus);
      return INIT_FAILED;
     }
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(SlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);
   trendHandle = iMA(_Symbol, SignalTF, TrendEMA, 0, MODE_EMA, PRICE_CLOSE);
   atrHandle   = iATR(_Symbol, SignalTF, ATRPeriod);
   ResetDailyCounters();
   EventSetMillisecondTimer(200);
   lastStatus = "waiting for python";
   Print("PythonBridgeEA ready. Files ", HookPrefix(), "  TCP ", EnableTcp);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   CloseTcp();
   if(trendHandle != INVALID_HANDLE) IndicatorRelease(trendHandle);
   if(atrHandle   != INVALID_HANDLE) IndicatorRelease(atrHandle);
  }

void OnTick()
  {
   BridgePump();
  }

void OnTimer()
  {
   BridgePump();
  }

void BridgePump()
  {
   ResetDailyCounters();
   WriteSnapshotFile();
   Comment("PythonBridgeEA ER-H4\n", lastStatus, "\n", AccountInfoString(ACCOUNT_SERVER),
           "  ", AccountInfoInteger(ACCOUNT_LOGIN),
           "\nequity ", DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2));

   string fileCmd = ReadCommandFile();
   if(fileCmd != "")
     {
      string result = HandleCommand(fileCmd);
      WriteResultFile(result);
      lastResult = result;
     }

   if(EnableTcp)
     {
      EnsureTcp();
      if(tcpSock != INVALID_HANDLE && SocketIsConnected(tcpSock))
        {
         lastStatus = "tcp connected";
         SendLine("{\"type\":\"poll\",\"token\":\"" + JsonEscape(HookToken) + "\",\"snapshot\":" + SnapshotJson() + "}");
         string line = ReadLine(180);
         if(line != "")
           {
            string result = HandleCommand(line);
            lastResult = result;
            SendLine(result);
           }
        }
      else
         lastStatus = "waiting for python (files still active)";
     }
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
      haltedToday = false;
     }
  }

bool DailyLossHit()
  {
   if(haltedToday)
      return true;
   if(dayStartEquity <= 0.0)
      return false;
   const double dd = (dayStartEquity - AccountInfoDouble(ACCOUNT_EQUITY)) / dayStartEquity * 100.0;
   if(dd >= DailyLossPercent)
     {
      haltedToday = true;
      Print("Daily loss cap hit: ", DoubleToString(dd, 2), "%");
      return true;
     }
   return false;
  }

bool DemoBlocked()
  {
   if(!RequireDemo || AllowLive)
      return false;
   return (AccountInfoInteger(ACCOUNT_TRADE_MODE) != ACCOUNT_TRADE_MODE_DEMO);
  }

int CountMagicPositions(const string symbolFilter)
  {
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;
      if(symbolFilter != "" && PositionGetString(POSITION_SYMBOL) != symbolFilter)
         continue;
      count++;
     }
   return count;
  }

int CountOurPositions()
  {
   return CountMagicPositions(_Symbol);
  }

int SpreadPoints(const string symbol)
  {
   const double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   if(point <= 0.0)
      return INT_MAX;
   return (int)MathRound((SymbolInfoDouble(symbol, SYMBOL_ASK) - SymbolInfoDouble(symbol, SYMBOL_BID)) / point);
  }

string HandleCommand(string json)
  {
   StringTrimLeft(json);
   StringTrimRight(json);
   if(json == "")
      return Result(true, "idle", 0, 0, "");

   const string cmd = JsonStr(json, "cmd", JsonStr(json, "type", ""));
   const string id  = JsonStr(json, "id", "");
   if(cmd == "idle" || cmd == "poll")
      return Result(true, "idle", 0, 0, "", id);

   const string token = JsonStr(json, "token", "");
   if(token != HookToken)
      return Result(false, "bad_token", 0, 0, "token mismatch", id);
   if(cmd == "ping" || cmd == "account" || cmd == "snapshot" || cmd == "status")
      return "{\"ok\":true,\"cmd\":\"" + cmd + "\",\"id\":\"" + JsonEscape(id) + "\",\"snapshot\":" + SnapshotJson() + "}";
   if(cmd == "halt")
     {
      haltedByPython = true;
      return Result(true, "halt", 0, 0, "halted", id);
     }
   if(cmd == "resume")
     {
      haltedByPython = false;
      return Result(true, "resume", 0, 0, "resumed", id);
     }
   if(cmd == "close")
      return DoClose(JsonLong(json, "ticket", 0), id);
   if(cmd == "close_all" || cmd == "close-all")
      return DoCloseAll(id);
   if(cmd == "modify")
      return DoModify(json, id);
   if(cmd == "buy" || cmd == "sell")
      return DoOpen(json, cmd, id);
   if(cmd == "signal")
      return SignalResponse(id);

   return Result(false, cmd, 0, 0, "unknown command", id);
  }

string DoOpen(const string json, const string cmd, const string id)
  {
   if(DemoBlocked())
      return Result(false, cmd, 0, 0, "live account blocked", id);
   if(haltedByPython)
      return Result(false, cmd, 0, 0, "halted by python", id);
   if(DailyLossHit())
      return Result(false, cmd, 0, 0, "daily loss cap", id);
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED) || !TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      return Result(false, cmd, 0, 0, "algo trading disabled", id);

   string symbol = JsonStr(json, "symbol", _Symbol);
   StringToUpper(symbol);
   if(!SymbolSelect(symbol, true))
      return Result(false, cmd, 0, 0, "unknown symbol", id);

   if(symbol != _Symbol)
      return Result(false, cmd, 0, 0, "attach EA on this symbol chart", id);

   if(CountMagicPositions(_Symbol) >= MaxOpenPositions)
      return Result(false, cmd, 0, 0, "max positions this symbol", id);
   if(CountMagicPositions("") >= MaxAccountPositions)
      return Result(false, cmd, 0, 0, "max account positions", id);
   if(SpreadPoints(symbol) > MaxSpreadPoints)
      return Result(false, cmd, 0, 0, "spread too wide", id);

   const ENUM_ORDER_TYPE type = (cmd == "buy") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   const double price = (type == ORDER_TYPE_BUY)
                        ? SymbolInfoDouble(symbol, SYMBOL_ASK)
                        : SymbolInfoDouble(symbol, SYMBOL_BID);

   double sl = JsonNum(json, "sl", 0);
   double tp = JsonNum(json, "tp", 0);
   double volume = JsonNum(json, "volume", 0);
   const double risk = JsonNum(json, "risk_percent", 0);
   const double atrMult = JsonNum(json, "atr_stop_mult", ATRStopMult);
   const double rr = JsonNum(json, "reward_ratio", RewardRatio);

   if(sl <= 0.0 || tp <= 0.0)
     {
      const double atr = CurrentATR(symbol);
      if(atr <= 0.0)
         return Result(false, cmd, 0, 0, "atr unavailable", id);
      const double slDist = atr * atrMult;
      const double tpDist = slDist * rr;
      if(type == ORDER_TYPE_BUY)
        {
         if(sl <= 0.0) sl = price - slDist;
         if(tp <= 0.0) tp = price + tpDist;
        }
      else
        {
         if(sl <= 0.0) sl = price + slDist;
         if(tp <= 0.0) tp = price - tpDist;
        }
     }

   sl = NormalizePrice(symbol, sl);
   tp = NormalizePrice(symbol, tp);

   if(volume <= 0.0)
      volume = VolumeForRisk(symbol, price, sl, (risk > 0.0) ? risk : 0.5);
   if(volume <= 0.0)
      return Result(false, cmd, 0, 0, "volume is 0", id);
   if(volume > MaxVolume)
      return Result(false, cmd, 0, 0, "volume exceeds MaxVolume", id);

   trade.SetExpertMagicNumber(MagicNumber);
   const string comment = JsonStr(json, "comment", "python-hook");
   const bool ok = (type == ORDER_TYPE_BUY)
                   ? trade.Buy(volume, symbol, price, sl, tp, comment)
                   : trade.Sell(volume, symbol, price, sl, tp, comment);
   if(!ok)
      return Result(false, cmd, (long)trade.ResultRetcode(), 0, trade.ResultRetcodeDescription(), id);
   return Result(true, cmd, (long)trade.ResultRetcode(), (long)trade.ResultOrder(), "opened", id, volume);
  }

string DoClose(const long ticket, const string id)
  {
   if(ticket <= 0)
      return Result(false, "close", 0, ticket, "bad ticket", id);
   if(!PositionSelectByTicket((ulong)ticket))
      return Result(false, "close", 0, ticket, "position not found", id);
   if((int)PositionGetInteger(POSITION_MAGIC) != MagicNumber)
      return Result(false, "close", 0, ticket, "not our magic", id);
   const bool ok = trade.PositionClose((ulong)ticket);
   if(!ok)
      return Result(false, "close", (long)trade.ResultRetcode(), ticket, trade.ResultRetcodeDescription(), id);
   return Result(true, "close", (long)trade.ResultRetcode(), ticket, "closed", id);
  }

string DoCloseAll(const string id)
  {
   int closed = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      if(trade.PositionClose(ticket))
         closed++;
     }
   return Result(true, "close_all", 0, closed, IntegerToString(closed) + " closed", id);
  }

string DoModify(const string json, const string id)
  {
   const long ticket = JsonLong(json, "ticket", 0);
   if(!PositionSelectByTicket((ulong)ticket))
      return Result(false, "modify", 0, ticket, "position not found", id);
   if((int)PositionGetInteger(POSITION_MAGIC) != MagicNumber)
      return Result(false, "modify", 0, ticket, "not our magic", id);
   const string symbol = PositionGetString(POSITION_SYMBOL);
   const double sl = NormalizePrice(symbol, JsonNum(json, "sl", PositionGetDouble(POSITION_SL)));
   const double tp = NormalizePrice(symbol, JsonNum(json, "tp", PositionGetDouble(POSITION_TP)));
   const bool ok = trade.PositionModify((ulong)ticket, sl, tp);
   if(!ok)
      return Result(false, "modify", (long)trade.ResultRetcode(), ticket, trade.ResultRetcodeDescription(), id);
   return Result(true, "modify", (long)trade.ResultRetcode(), ticket, "modified", id);
  }

double CurrentATR(const string symbol)
  {
   if(symbol == _Symbol && atrHandle != INVALID_HANDLE)
     {
      double buf[];
      ArraySetAsSeries(buf, true);
      if(CopyBuffer(atrHandle, 0, 1, 1, buf) >= 1)
         return buf[0];
     }
   const int handle = iATR(symbol, PERIOD_H1, 14);
   if(handle == INVALID_HANDLE)
      return 0.0;
   double buf[];
   ArraySetAsSeries(buf, true);
   const int n = CopyBuffer(handle, 0, 1, 1, buf);
   IndicatorRelease(handle);
   if(n < 1)
      return 0.0;
   return buf[0];
  }

bool InSession()
  {
   MqlDateTime now;
   TimeToStruct(TimeCurrent(), now);
   return (now.day_of_week != 0 && now.day_of_week != 6);
  }

double EfficiencyRatio(const int period, const int shift)
  {
   const double c0 = iClose(_Symbol, SignalTF, shift);
   const double cn = iClose(_Symbol, SignalTF, shift + period);
   if(c0 <= 0.0 || cn <= 0.0)
      return 0.0;
   const double change = MathAbs(c0 - cn);
   double vol = 0.0;
   for(int k = 0; k < period; k++)
     {
      const double a = iClose(_Symbol, SignalTF, shift + k);
      const double b = iClose(_Symbol, SignalTF, shift + k + 1);
      vol += MathAbs(a - b);
     }
   if(vol <= 0.0)
      return 0.0;
   return change / vol;
  }

bool H4Values(double &ema50, double &ema50Prev, double &closeNow, double &closePrev, double &atr, double &er, datetime &barTime)
  {
   ema50 = ema50Prev = closeNow = closePrev = atr = er = 0.0;
   barTime = 0;
   double ma[], at[];
   datetime t[];
   ArraySetAsSeries(ma, true);
   ArraySetAsSeries(at, true);
   ArraySetAsSeries(t, true);
   if(CopyBuffer(trendHandle, 0, 0, 4, ma) < 4) return false;
   if(CopyBuffer(atrHandle, 0, 0, 3, at) < 3) return false;
   if(CopyTime(_Symbol, SignalTF, 0, 3, t) < 3) return false;
   closeNow = iClose(_Symbol, SignalTF, 1);
   closePrev = iClose(_Symbol, SignalTF, 2);
   if(closeNow <= 0.0 || closePrev <= 0.0) return false;
   ema50 = ma[1];
   ema50Prev = ma[2];
   atr = at[1];
   er = EfficiencyRatio(ErPeriod, 1);
   barTime = t[1];
   return true;
  }

string SignalResponse(const string id)
  {
   double ema50, ema50Prev, closeNow, closePrev, atr, er;
   datetime barTime;
   string side = "flat";
   string reason = "no setup";
   if(!InSession())
      reason = "weekend";
   else if(SpreadPoints(_Symbol) > MaxSpreadPoints)
      reason = "spread too wide";
   else if(!H4Values(ema50, ema50Prev, closeNow, closePrev, atr, er, barTime))
      reason = "indicators not ready";
   else if(er < ErMin)
      reason = "chop (low efficiency ratio)";
   else if(closeNow > ema50 && closePrev <= ema50Prev)
     {
      side = "buy";
      reason = "ER trend + close crossed above EMA50";
     }
   else if(closeNow < ema50 && closePrev >= ema50Prev)
     {
      side = "sell";
      reason = "ER trend + close crossed below EMA50";
     }
   else
      reason = "efficient trend, no EMA50 cross";
   return "{\"ok\":true,\"cmd\":\"signal\",\"id\":\"" + JsonEscape(id) +
          "\",\"side\":\"" + side +
          "\",\"reason\":\"" + JsonEscape(reason) +
          "\",\"snapshot\":" + SnapshotJson() + "}";
  }

double NormalizePrice(const string symbol, const double price)
  {
   const double tick = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   const int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   if(tick <= 0.0)
      return NormalizeDouble(price, digits);
   return NormalizeDouble(MathRound(price / tick) * tick, digits);
  }

double VolumeForRisk(const string symbol, const double entry, const double sl, const double riskPercent)
  {
   const double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   const double riskMoney = equity * (riskPercent / 100.0);
   const double tickSize = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   const double tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   const double lossDistance = MathAbs(entry - sl);
   if(tickSize <= 0.0 || tickValue <= 0.0 || lossDistance <= 0.0)
      return 0.0;
   const double ticks = lossDistance / tickSize;
   const double rawLots = riskMoney / (ticks * tickValue);
   const double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   const double maxLot = MathMin(MaxVolume, SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX));
   const double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0.0)
      return 0.0;
   double lots = MathFloor(rawLots / step) * step;
   lots = MathMax(minLot, MathMin(maxLot, lots));
   const int volDigits = (step >= 1.0) ? 0 : (int)MathCeil(-MathLog10(step));
   lots = NormalizeDouble(lots, volDigits);
   const double minLotRisk = minLot * ticks * tickValue;
   if(minLotRisk > riskMoney * 1.5)
      return 0.0;
   return lots;
  }

string SnapshotJson()
  {
   string mode = "unknown";
   const long tm = AccountInfoInteger(ACCOUNT_TRADE_MODE);
   if(tm == ACCOUNT_TRADE_MODE_DEMO) mode = "demo";
   else if(tm == ACCOUNT_TRADE_MODE_CONTEST) mode = "contest";
   else if(tm == ACCOUNT_TRADE_MODE_REAL) mode = "real";

   string pos = "[";
   bool first = true;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      if(!first)
         pos += ",";
      first = false;
      const int ptype = (int)PositionGetInteger(POSITION_TYPE);
      pos += "{\"ticket\":" + IntegerToString((long)ticket) +
             ",\"symbol\":\"" + JsonEscape(PositionGetString(POSITION_SYMBOL)) +
             "\",\"type\":\"" + ((ptype == POSITION_TYPE_BUY) ? "buy" : "sell") +
             "\",\"volume\":" + DoubleToString(PositionGetDouble(POSITION_VOLUME), 2) +
             ",\"price_open\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN), (int)SymbolInfoInteger(PositionGetString(POSITION_SYMBOL), SYMBOL_DIGITS)) +
             ",\"sl\":" + DoubleToString(PositionGetDouble(POSITION_SL), 5) +
             ",\"tp\":" + DoubleToString(PositionGetDouble(POSITION_TP), 5) +
             ",\"profit\":" + DoubleToString(PositionGetDouble(POSITION_PROFIT), 2) + "}";
     }
   pos += "]";

   return "{\"login\":" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) +
          ",\"server\":\"" + JsonEscape(AccountInfoString(ACCOUNT_SERVER)) +
          "\",\"company\":\"" + JsonEscape(AccountInfoString(ACCOUNT_COMPANY)) +
          "\",\"mode\":\"" + mode +
          "\",\"currency\":\"" + JsonEscape(AccountInfoString(ACCOUNT_CURRENCY)) +
          "\",\"balance\":" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) +
          ",\"equity\":" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) +
          ",\"margin\":" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN), 2) +
          ",\"free_margin\":" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2) +
          ",\"leverage\":" + IntegerToString(AccountInfoInteger(ACCOUNT_LEVERAGE)) +
          ",\"positions_total\":" + IntegerToString(CountMagicPositions(_Symbol)) +
          ",\"positions_account\":" + IntegerToString(CountMagicPositions("")) +
          ",\"halted_daily\":" + (DailyLossHit() ? "true" : "false") +
          ",\"halted_python\":" + (haltedByPython ? "true" : "false") +
          ",\"algo_allowed\":" + ((MQLInfoInteger(MQL_TRADE_ALLOWED) && TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) ? "true" : "false") +
          ",\"symbol\":\"" + JsonEscape(_Symbol) +
          "\",\"bid\":" + DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_BID), _Digits) +
          ",\"ask\":" + DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_ASK), _Digits) +
          ",\"session_ok\":" + (InSession() ? "true" : "false") +
          MarketJson() +
          ",\"time\":\"" + TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) +
          "\",\"positions\":" + pos + "}";
  }

string MarketJson()
  {
   double ema50, ema50Prev, closeNow, closePrev, atr, er;
   datetime barTime;
   if(!H4Values(ema50, ema50Prev, closeNow, closePrev, atr, er, barTime))
      return ",\"market_ready\":false";
   return ",\"market_ready\":true" +
          ",\"strategy\":\"er_ema50_h4\"" +
          ",\"er\":" + DoubleToString(er, 3) +
          ",\"ema50\":" + DoubleToString(ema50, _Digits) +
          ",\"close\":" + DoubleToString(closeNow, _Digits) +
          ",\"atr\":" + DoubleToString(atr, _Digits) +
          ",\"bar_time\":\"" + TimeToString(barTime, TIME_DATE|TIME_MINUTES) + "\"";
  }

string Result(const bool ok, const string cmd, const long retcode, const long ticket,
              const string message, const string id = "", const double volume = 0.0)
  {
   return "{\"ok\":" + (ok ? "true" : "false") +
          ",\"cmd\":\"" + JsonEscape(cmd) +
          "\",\"id\":\"" + JsonEscape(id) +
          "\",\"retcode\":" + IntegerToString(retcode) +
          ",\"ticket\":" + IntegerToString(ticket) +
          ",\"volume\":" + DoubleToString(volume, 2) +
          ",\"message\":\"" + JsonEscape(message) +
          "\",\"snapshot\":" + SnapshotJson() + "}";
  }

void WriteSnapshotFile()
  {
   WriteCommon(HookPrefix() + "_snapshot.json", SnapshotJson());
  }

void WriteResultFile(const string json)
  {
   WriteCommon(HookPrefix() + "_result.json", json);
  }

string HookPrefix()
  {
   string s = _Symbol;
   StringReplace(s, ".", "_");
   StringReplace(s, "#", "_");
   StringReplace(s, "/", "");
   return "mt5_hook_" + s;
  }

string ReadAndDeleteCommon(const string name)
  {
   if(!FileIsExist(name, FILE_COMMON))
      return "";
   const int h = FileOpen(name, FILE_READ|FILE_TXT|FILE_ANSI|FILE_SHARE_READ|FILE_COMMON);
   if(h == INVALID_HANDLE)
      return "";
   string body = "";
   while(!FileIsEnding(h))
      body += FileReadString(h);
   FileClose(h);
   FileDelete(name, FILE_COMMON);
   return body;
  }

string ReadCommandFile()
  {
   string body = ReadAndDeleteCommon(HookPrefix() + "_cmd.json");
   if(body != "")
      return body;
   // One-chart leftover from v1.04. Only this symbol may consume it.
   body = ReadAndDeleteCommon("mt5_hook_cmd.json");
   if(body == "")
      return "";
   const string cmdSym = JsonStr(body, "symbol", "");
   if(cmdSym != "" && cmdSym != _Symbol)
      return "";
   return body;
  }

void WriteCommon(const string name, const string body)
  {
   const int h = FileOpen(name, FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_SHARE_READ|FILE_COMMON);
   if(h == INVALID_HANDLE)
      return;
   FileWriteString(h, body);
   FileClose(h);
  }

void EnsureTcp()
  {
   if(tcpSock != INVALID_HANDLE && SocketIsConnected(tcpSock))
      return;
   CloseTcp();
   const uint now = GetTickCount();
   if(lastTcpTry != 0 && (now - lastTcpTry) < 3000)
      return;
   lastTcpTry = now;
   tcpSock = SocketCreate();
   if(tcpSock == INVALID_HANDLE)
      return;
   if(!SocketConnect(tcpSock, HookHost, (uint)HookPort, 250))
     {
      CloseTcp();
      return;
     }
   tcpRx = "";
  }

void CloseTcp()
  {
   if(tcpSock != INVALID_HANDLE)
     {
      SocketClose(tcpSock);
      tcpSock = INVALID_HANDLE;
     }
   tcpRx = "";
  }

void SendLine(const string s)
  {
   if(tcpSock == INVALID_HANDLE)
      return;
   string line = s + "\n";
   uchar buf[];
   const int n = StringToCharArray(line, buf, 0, WHOLE_ARRAY, CP_UTF8);
   if(n > 1)
      SocketSend(tcpSock, buf, n - 1);
  }

string ReadLine(const uint timeout_ms)
  {
   if(tcpSock == INVALID_HANDLE)
      return "";
   const uint start = GetTickCount();
   while(GetTickCount() - start < timeout_ms)
     {
      uchar buf[2048];
      const int n = SocketRead(tcpSock, buf, 2048, 20);
      if(n > 0)
         tcpRx += CharArrayToString(buf, 0, n, CP_UTF8);
      const int nl = StringFind(tcpRx, "\n");
      if(nl >= 0)
        {
         string line = StringSubstr(tcpRx, 0, nl);
         tcpRx = StringSubstr(tcpRx, nl + 1);
         StringTrimRight(line);
         return line;
        }
      if(n <= 0)
         Sleep(10);
     }
   return "";
  }

string JsonEscape(const string s)
  {
   string o = s;
   StringReplace(o, "\\", "\\\\");
   StringReplace(o, "\"", "\\\"");
   StringReplace(o, "\n", "\\n");
   return o;
  }

string JsonStr(const string json, const string key, const string def = "")
  {
   const string needle = "\"" + key + "\"";
   const int p = StringFind(json, needle);
   if(p < 0)
      return def;
   const int colon = StringFind(json, ":", p + StringLen(needle));
   if(colon < 0)
      return def;
   int i = colon + 1;
   const int n = StringLen(json);
   while(i < n && (StringGetCharacter(json, i) == ' ' || StringGetCharacter(json, i) == '\t'))
      i++;
   if(i < n && StringGetCharacter(json, i) == '"')
     {
      const int start = i + 1;
      const int end = StringFind(json, "\"", start);
      if(end < 0)
         return def;
      return StringSubstr(json, start, end - start);
     }
   int end = i;
   while(end < n)
     {
      const int c = StringGetCharacter(json, end);
      if(c == ',' || c == '}' || c == ' ' || c == '\n' || c == '\r')
         break;
      end++;
     }
   const string raw = StringSubstr(json, i, end - i);
   return (raw == "") ? def : raw;
  }

double JsonNum(const string json, const string key, const double def = 0.0)
  {
   const string s = JsonStr(json, key, "");
   if(s == "" || s == "null")
      return def;
   return StringToDouble(s);
  }

long JsonLong(const string json, const string key, const long def = 0)
  {
   const string s = JsonStr(json, key, "");
   if(s == "" || s == "null")
      return def;
   return StringToInteger(s);
  }
