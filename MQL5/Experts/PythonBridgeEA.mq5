//+------------------------------------------------------------------+
//| PythonBridgeEA.mq5                                               |
//| Executes commands from the local Python hook. Demo-only default. |
//| Not financial advice. No martingale / grid / averaging.          |
//+------------------------------------------------------------------+
#property copyright "mt5-demo-bot"
#property version   "1.14"
#property description "Autonomous Kaufman ER / EMA50 H4 trend. Fixed 2R target. On-chart HUD."

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

input group "=== Autonomous ==="
input bool   AutoTrade            = true;   // EA entries + exits; do not also run the Python manager
input double RiskPercent          = 1.0;
input int    MaxUsdDir            = 2;      // max same-way USD bets across magic positions
input int    MaxTradesPerDay      = 1;

input group "=== Signal (Kaufman ER + EMA50 on H4) ==="
input ENUM_TIMEFRAMES SignalTF    = PERIOD_H4;
input int    ErPeriod             = 10;
input double ErMin                = 0.40;
input int    TrendEMA             = 50;
input int    ATRPeriod            = 14;
input double ATRStopMult          = 2.5;    // initial stop distance
input double RewardRatio          = 2.0;    // take-profit in R (0 = no TP)
input bool   ShowEmaOnChart       = true;   // plot the signal EMA50 on this chart

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
datetime lastBarTime = 0;
datetime emaBarStamp = 0;

void SignalCloseness(double &frac, string &text, color &fill, color &labelClr);
bool H4Values(double &ema50, double &ema50Prev, double &closeNow, double &closePrev, double &atr, double &er, datetime &barTime);

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
   lastStatus = AutoTrade ? "autotrade on" : "hook only";
   Print("PythonBridgeEA ready. AutoTrade=", AutoTrade, " files ", HookPrefix(), " TCP ", EnableTcp);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   CloseTcp();
   Comment("");
   ObjectsDeleteAll(0, "PBHUD_");
   ObjectsDeleteAll(0, "PBLINE_");
   ObjectsDeleteAll(0, "PBEMA_");
   ChartRedraw(0);
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

void HudLabel(const string name, const int x, const int y, const int size, const color clr)
  {
   if(ObjectFind(0, name) < 0)
     {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
     }
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, size);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
  }

void HudRect(const string name, const int x, const int y, const int w, const int h, const color fill)
  {
   if(ObjectFind(0, name) < 0)
     {
      ObjectCreate(0, name, OBJ_RECTANGLE_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_BORDER_TYPE, BORDER_FLAT);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
      ObjectSetInteger(0, name, OBJPROP_BACK, false);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
     }
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, MathMax(w, 1));
   ObjectSetInteger(0, name, OBJPROP_YSIZE, MathMax(h, 1));
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, fill);
   ObjectSetInteger(0, name, OBJPROP_COLOR, fill);
  }

void HudMeter(const string prefix, const int x, const int y, const int w, const int h,
              const double frac, const double gate, const color fill)
  {
   const double f = MathMax(0.0, MathMin(1.0, frac));
   HudRect(prefix + "_TR", x, y, w, h, C'40,40,46');
   ObjectSetInteger(0, prefix + "_TR", OBJPROP_ZORDER, 0);
   const int fw = (int)MathRound(w * f);
   if(fw < 1)
      ObjectDelete(0, prefix + "_FL");
   else
     {
      HudRect(prefix + "_FL", x, y, fw, h, fill);
      ObjectSetInteger(0, prefix + "_FL", OBJPROP_ZORDER, 1);
     }
   if(gate > 0.0 && gate < 1.0)
     {
      const int gx = x + (int)MathRound(w * gate);
      HudRect(prefix + "_GT", gx, y - 1, 2, h + 2, C'230,230,236');
      ObjectSetInteger(0, prefix + "_GT", OBJPROP_ZORDER, 2);
     }
  }

void HudLine(const string name, const double price, const color clr, const string caption)
  {
   if(price <= 0.0)
     {
      ObjectDelete(0, name);
      return;
     }
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);
   ObjectSetDouble(0, name, OBJPROP_PRICE, price);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_DASH);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetString(0, name, OBJPROP_TEXT, caption);
  }

void DrawEmaSeg(const int i, const datetime tA, const double pA, const datetime tB, const double pB)
  {
   const string name = "PBEMA_" + IntegerToString(i);
   if(ObjectFind(0, name) < 0)
     {
      ObjectCreate(0, name, OBJ_TREND, 0, tA, pA, tB, pB);
      ObjectSetInteger(0, name, OBJPROP_COLOR, C'232,168,48');
      ObjectSetInteger(0, name, OBJPROP_WIDTH, 2);
      ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_SOLID);
      ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, name, OBJPROP_RAY_LEFT, false);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
     }
   ObjectSetInteger(0, name, OBJPROP_TIME, 0, tA);
   ObjectSetDouble(0, name, OBJPROP_PRICE, 0, pA);
   ObjectSetInteger(0, name, OBJPROP_TIME, 1, tB);
   ObjectSetDouble(0, name, OBJPROP_PRICE, 1, pB);
  }

void DrawEmaCurve()
  {
   if(!ShowEmaOnChart || trendHandle == INVALID_HANDLE)
      return;
   const int n = 150;
   double ma[];
   ArraySetAsSeries(ma, true);
   if(CopyBuffer(trendHandle, 0, 0, n, ma) < n)
      return;
   const datetime t0 = iTime(_Symbol, SignalTF, 0);
   const int last = (t0 != emaBarStamp) ? n - 1 : 1;
   for(int i = 0; i < last; i++)
     {
      const datetime ta = iTime(_Symbol, SignalTF, i + 1);
      const datetime tb = iTime(_Symbol, SignalTF, i);
      if(ta <= 0 || tb <= 0 || ma[i] <= 0.0 || ma[i + 1] <= 0.0)
         continue;
      DrawEmaSeg(i, ta, ma[i + 1], tb, ma[i]);
     }
   emaBarStamp = t0;
   ObjectDelete(0, "PBLINE_EMA");
  }

void DrawBoard()
  {
   DrawEmaCurve();
   const int panelW = 348;
   const int panelH = 214;
   HudRect("PBHUD_BG", 8, 18, panelW, panelH, C'22,22,26');
   ObjectSetInteger(0, "PBHUD_BG", OBJPROP_COLOR, C'58,58,64');
   ObjectSetInteger(0, "PBHUD_BG", OBJPROP_BORDER_TYPE, BORDER_FLAT);
   ObjectSetInteger(0, "PBHUD_BG", OBJPROP_WIDTH, 1);

   const double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double dayDd = 0.0;
   if(dayStartEquity > 0.0)
      dayDd = (dayStartEquity - equity) / dayStartEquity * 100.0;
   string halt = "ok";
   if(haltedToday)
      halt = "DAILY HALT";
   else if(haltedByPython)
      halt = "PYTHON HALT";
   else if(!AutoTrade)
      halt = "HOOK ONLY";

   string posLine = "flat";
   double entry = 0.0, sl = 0.0, tp = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      const bool isBuy = ((int)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      entry = PositionGetDouble(POSITION_PRICE_OPEN);
      sl = PositionGetDouble(POSITION_SL);
      tp = PositionGetDouble(POSITION_TP);
      posLine = (isBuy ? "BUY " : "SELL ") +
                DoubleToString(PositionGetDouble(POSITION_VOLUME), 2) +
                "   P/L " + DoubleToString(PositionGetDouble(POSITION_PROFIT), 2);
      break;
     }

   ObjectDelete(0, "PBHUD_NRL");
   ObjectDelete(0, "PBHUD_NR_TR");
   ObjectDelete(0, "PBHUD_NR_FL");
   ObjectDelete(0, "PBHUD_NR_GT");

   double ema50 = 0, ema50Prev = 0, closeNow = 0, closePrev = 0, atr = 0, er = 0;
   datetime barTime = 0;
   const bool ready = H4Values(ema50, ema50Prev, closeNow, closePrev, atr, er, barTime);
   const bool armed = (ready && er >= ErMin);

   double sigFrac = 0.0;
   string sigText = "to signal  warming up";
   color sigFill = C'100,110,150';
   color sigLbl = C'160,160,168';
   SignalCloseness(sigFrac, sigText, sigFill, sigLbl);

   HudLabel("PBHUD_T1", 20, 26, 11, C'220,220,224');
   ObjectSetString(0, "PBHUD_T1", OBJPROP_TEXT, "PythonBridgeEA  1.14   " + (AutoTrade ? "AUTO" : "HOOK"));
   HudLabel("PBHUD_T2", 20, 46, 9, C'160,160,168');
   ObjectSetString(0, "PBHUD_T2", OBJPROP_TEXT,
                   _Symbol + "  H4   risk " + DoubleToString(RiskPercent, 1) + "%   2.5 ATR / 2R");
   HudLabel("PBHUD_T3", 20, 64, 9, C'200,200,206');
   ObjectSetString(0, "PBHUD_T3", OBJPROP_TEXT,
                   "equity " + DoubleToString(equity, 2) +
                   "   day " + DoubleToString(dayDd, 2) + "% / " + DoubleToString(DailyLossPercent, 0) + "%");
   HudLabel("PBHUD_T4", 20, 82, 9, (StringFind(halt, "HALT") >= 0) ? C'220,90,90' : C'120,190,130');
   ObjectSetString(0, "PBHUD_T4", OBJPROP_TEXT, "status  " + halt);
   HudLabel("PBHUD_T5", 20, 100, 9, C'180,180,186');
   ObjectSetString(0, "PBHUD_T5", OBJPROP_TEXT, "setup   " + lastStatus);
   HudLabel("PBHUD_T6", 20, 118, 9, C'200,200,206');
   ObjectSetString(0, "PBHUD_T6", OBJPROP_TEXT, "trade   " + posLine);

   const int barX = 20;
   const int barW = 316;
   HudLabel("PBHUD_ERL", barX, 138, 8, armed ? C'120,190,130' : C'200,150,80');
   ObjectSetString(0, "PBHUD_ERL", OBJPROP_TEXT,
                   ready
                   ? ("last H4 ER  " + DoubleToString(er, 2) + " / 1.00   gate " + DoubleToString(ErMin, 2) +
                      (armed ? "   ARMED" : "   chop"))
                   : "last H4 ER  warming up");
   HudMeter("PBHUD_ER", barX, 154, barW, 10, ready ? er : 0.0, ErMin, armed ? C'70,180,120' : C'200,140,70');

   HudLabel("PBHUD_SGL", barX, 170, 8, sigLbl);
   ObjectSetString(0, "PBHUD_SGL", OBJPROP_TEXT, sigText);
   HudMeter("PBHUD_SG", barX, 186, barW, 10, sigFrac, 0.0, sigFill);

   HudLabel("PBHUD_T7", 20, 204, 8, C'120,120,128');
   ObjectSetString(0, "PBHUD_T7", OBJPROP_TEXT,
                   AccountInfoString(ACCOUNT_SERVER) + "  " + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)));

   HudLine("PBLINE_EN", entry, C'90,140,220', "entry");
   HudLine("PBLINE_SL", sl, C'200,80,80', "SL");
   HudLine("PBLINE_TP", tp, C'70,170,110', "TP");
   ChartRedraw(0);
  }

void BridgePump()
  {
   ResetDailyCounters();
   if(AutoTrade)
      TryEnter();
   WriteSnapshotFile();
   DrawBoard();
   Comment("");

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

   if(sl <= 0.0 || (tp <= 0.0 && rr > 0.0))
     {
      const double atr = CurrentATR(symbol);
      if(atr <= 0.0)
         return Result(false, cmd, 0, 0, "atr unavailable", id);
      const double slDist = atr * atrMult;
      const double tpDist = slDist * rr;
      if(type == ORDER_TYPE_BUY)
        {
         if(sl <= 0.0) sl = price - slDist;
         if(tp <= 0.0 && rr > 0.0) tp = price + tpDist;
        }
      else
        {
         if(sl <= 0.0) sl = price + slDist;
         if(tp <= 0.0 && rr > 0.0) tp = price - tpDist;
        }
     }

   sl = NormalizePrice(symbol, sl);
   tp = NormalizePrice(symbol, tp);

   if(volume <= 0.0)
      volume = VolumeForRisk(symbol, price, sl, (risk > 0.0) ? risk : RiskPercent);
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

int UsdDirOf(const string symbol, const bool isBuy)
  {
   string s = symbol;
   StringToUpper(s);
   StringReplace(s, ".", "");
   StringReplace(s, "_", "");
   if(StringLen(s) < 6)
      return 0;
   const string base = StringSubstr(s, 0, 3);
   const string quote = StringSubstr(s, 3, 3);
   if(quote == "USD")
      return isBuy ? -1 : 1;
   if(base == "USD")
      return isBuy ? 1 : -1;
   return 0;
  }

int CountUsdDir(const int dir)
  {
   if(dir == 0)
      return 0;
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;
      const bool isBuy = ((int)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      if(UsdDirOf(PositionGetString(POSITION_SYMBOL), isBuy) == dir)
         n++;
     }
   return n;
  }

int CountEntriesToday()
  {
   if(dayStamp <= 0)
      return 0;
   if(!HistorySelect(dayStamp, TimeCurrent()))
      return 0;
   int n = 0;
   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0)
         continue;
      if((int)HistoryDealGetInteger(ticket, DEAL_MAGIC) != MagicNumber)
         continue;
      if(HistoryDealGetString(ticket, DEAL_SYMBOL) != _Symbol)
         continue;
      if((int)HistoryDealGetInteger(ticket, DEAL_ENTRY) != DEAL_ENTRY_IN)
         continue;
      n++;
     }
   return n;
  }

string SignalSide(string &reason)
  {
   double ema50, ema50Prev, closeNow, closePrev, atr, er;
   datetime barTime;
   string side = "flat";
   reason = "no setup";
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
   return side;
  }

void TryEnter()
  {
   datetime t[];
   ArraySetAsSeries(t, true);
   if(CopyTime(_Symbol, SignalTF, 0, 1, t) < 1)
      return;
   if(lastBarTime == 0)
     {
      lastBarTime = t[0];
      return;
     }
   if(t[0] == lastBarTime)
      return;
   lastBarTime = t[0];

   if(DemoBlocked() || haltedByPython || DailyLossHit())
      return;
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED) || !TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      return;
   if(CountMagicPositions(_Symbol) >= MaxOpenPositions)
      return;
   if(CountMagicPositions("") >= MaxAccountPositions)
      return;
   if(CountEntriesToday() >= MaxTradesPerDay)
      return;
   if(SpreadPoints(_Symbol) > MaxSpreadPoints)
      return;

   string reason;
   const string side = SignalSide(reason);
   lastStatus = reason;
   if(side != "buy" && side != "sell")
      return;

   const int dir = UsdDirOf(_Symbol, side == "buy");
   if(dir != 0 && CountUsdDir(dir) >= MaxUsdDir)
     {
      lastStatus = "usd cap";
      return;
     }

   const double atr = CurrentATR(_Symbol);
   if(atr <= 0.0)
      return;
   const double price = (side == "buy") ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   const double slDist = atr * ATRStopMult;
   double sl = (side == "buy") ? price - slDist : price + slDist;
   double tp = 0.0;
   if(RewardRatio > 0.0)
      tp = (side == "buy") ? price + slDist * RewardRatio : price - slDist * RewardRatio;
   sl = NormalizePrice(_Symbol, sl);
   if(tp > 0.0)
      tp = NormalizePrice(_Symbol, tp);
   const double volume = VolumeForRisk(_Symbol, price, sl, RiskPercent);
   if(volume <= 0.0 || volume > MaxVolume)
      return;

   trade.SetExpertMagicNumber(MagicNumber);
   const bool ok = (side == "buy")
                   ? trade.Buy(volume, _Symbol, price, sl, tp, "ea-er-h4")
                   : trade.Sell(volume, _Symbol, price, sl, tp, "ea-er-h4");
   if(ok)
      lastStatus = "opened " + side;
   else
      lastStatus = "entry failed " + trade.ResultRetcodeDescription();
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

bool H4Forming(double &ema50, double &ema50Prev, double &closeNow, double &closePrev, double &atr, double &er)
  {
   ema50 = ema50Prev = closeNow = closePrev = atr = er = 0.0;
   double ma[], at[];
   ArraySetAsSeries(ma, true);
   ArraySetAsSeries(at, true);
   if(CopyBuffer(trendHandle, 0, 0, 3, ma) < 3) return false;
   if(CopyBuffer(atrHandle, 0, 0, 2, at) < 2) return false;
   closeNow = iClose(_Symbol, SignalTF, 0);
   closePrev = iClose(_Symbol, SignalTF, 1);
   if(closeNow <= 0.0 || closePrev <= 0.0) return false;
   ema50 = ma[0];
   ema50Prev = ma[1];
   atr = at[0];
   if(atr <= 0.0)
      atr = at[1];
   er = EfficiencyRatio(ErPeriod, 0);
   return (ema50 > 0.0 && atr > 0.0);
  }

string MinutesLeftH4()
  {
   const datetime open = iTime(_Symbol, SignalTF, 0);
   if(open <= 0)
      return "";
   int sec = (int)(PeriodSeconds(SignalTF) - (TimeCurrent() - open));
   if(sec < 0)
      sec = 0;
   return IntegerToString(sec / 3600) + "h" + IntegerToString((sec % 3600) / 60) + "m";
  }

void SignalCloseness(double &frac, string &text, color &fill, color &labelClr)
  {
   frac = 0.0;
   fill = C'100,110,150';
   labelClr = C'160,160,168';
   text = "to signal  warming up";

   double ema50, ema50Prev, closeNow, closePrev, atr, er;
   if(!H4Forming(ema50, ema50Prev, closeNow, closePrev, atr, er))
      return;

   const double erPart = MathMax(0.0, MathMin(1.0, er / ErMin));
   const bool wantBuy = (closePrev <= ema50Prev);
   const bool wantSell = (closePrev >= ema50Prev);
   double buyPart = 0.0;
   double sellPart = 0.0;
   if(wantBuy)
      buyPart = (closeNow > ema50) ? 1.0 : 1.0 - MathMin(MathAbs(ema50 - closeNow) / atr, 1.0);
   if(wantSell)
      sellPart = (closeNow < ema50) ? 1.0 : 1.0 - MathMin(MathAbs(closeNow - ema50) / atr, 1.0);

   const bool buyCross = (wantBuy && closeNow > ema50);
   const bool sellCross = (wantSell && closeNow < ema50);
   const double crossPart = MathMax(buyPart, sellPart);
   const bool crossed = (buyCross || sellCross);
   const string crossSide = (buyPart >= sellPart) ? "buy" : "sell";
   frac = MathMin(erPart, crossPart);

   const string eta = MinutesLeftH4();
   string wait = "";
   if(erPart < 1.0 && !crossed)
      wait = "need ER " + DoubleToString(er, 2) + "/" + DoubleToString(ErMin, 2) +
             " + " + DoubleToString(1.0 - crossPart, 2) + " ATR " + crossSide;
   else if(erPart < 1.0)
      wait = "need ER " + DoubleToString(er, 2) + "/" + DoubleToString(ErMin, 2) + " (cross ok)";
   else if(!crossed)
      wait = DoubleToString(1.0 - crossPart, 2) + " ATR to " + crossSide + " cross";
   else
      wait = "READY  fires in " + eta;

   string block = "";
   if(!InSession())
      block = "weekend";
   else if(haltedToday || DailyLossHit())
      block = "daily halt";
   else if(haltedByPython)
      block = "python halt";
   else if(!AutoTrade)
      block = "hook only";
   else if(CountMagicPositions(_Symbol) >= MaxOpenPositions)
      block = "already in";
   else if(CountEntriesToday() >= MaxTradesPerDay)
      block = "1 per day";
   else if(CountMagicPositions("") >= MaxAccountPositions)
      block = "account cap";
   else if(SpreadPoints(_Symbol) > MaxSpreadPoints)
      block = "spread";

   if(crossed && erPart >= 1.0)
     {
      if(block != "")
        {
         text = "to signal  100%  blocked: " + block;
         fill = C'200,140,70';
         labelClr = C'200,150,80';
        }
      else
        {
         text = "to signal  100%  " + wait;
         fill = C'70,180,120';
         labelClr = C'120,190,130';
        }
     }
   else
     {
      text = "to signal  " + IntegerToString((int)MathRound(frac * 100)) + "%  " + wait + "  " + eta;
      fill = (erPart >= 1.0) ? C'90,140,220' : C'200,140,70';
      labelClr = (erPart >= 1.0) ? C'140,170,220' : C'200,150,80';
     }
  }

string SignalResponse(const string id)
  {
   string reason;
   const string side = SignalSide(reason);
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
          ",\"auto_trade\":" + (AutoTrade ? "true" : "false") +
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
