// Compile as an MT5 Service and start one instance in each monitored terminal.
// Reports chart attachment only; it never places or changes trades.
#property service
#property version "1.00"

input int ScanIntervalSeconds = 30;

void WriteInventory()
  {
   const string temporary = "dashboard-eas.tmp.tsv";
   const string current = "dashboard-eas.tsv";
   int file = FileOpen(temporary, FILE_WRITE | FILE_CSV | FILE_UNICODE, '\t');
   if(file == INVALID_HANDLE)
     {
      PrintFormat("Dashboard EA probe: FileOpen failed (%d)", GetLastError());
      return;
     }

   FileWrite(file, "EA_PROBE_V1", (long)TimeGMT(),
             (long)AccountInfoInteger(ACCOUNT_LOGIN), AccountInfoString(ACCOUNT_SERVER));
   long chart = ChartFirst();
   int count = 0;
   for(; chart >= 0 && count < 500 && !IsStopped(); count++)
     {
      string expert = ChartGetString(chart, CHART_EXPERT_NAME);
      if(expert != "")
         FileWrite(file, chart, expert, ChartSymbol(chart), EnumToString(ChartPeriod(chart)));
      chart = ChartNext(chart);
     }
   FileClose(file);
   if(IsStopped())
      return;
   if(chart >= 0)
     {
      Print("Dashboard EA probe: more than 500 charts; keeping the previous complete report");
      FileDelete(temporary);
      return;
     }
   if(!FileMove(temporary, 0, current, FILE_REWRITE))
      PrintFormat("Dashboard EA probe: FileMove failed (%d)", GetLastError());
  }

void OnStart()
  {
   int interval = (ScanIntervalSeconds < 5 ? 5 : ScanIntervalSeconds);
   while(!IsStopped())
     {
      WriteInventory();
      for(int second = 0; second < interval && !IsStopped(); second++)
         Sleep(1000);
     }
  }
