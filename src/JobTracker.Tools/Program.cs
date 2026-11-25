using JobTracker.Tools;

// import-tracker has its own guard (real database allowed, with flags); the other commands are demo-only.
return args.Length > 0 && args[0] == "import-tracker"
    ? ImportCli.Run(args[1..], Console.Out, Console.Error)
    : ToolsCli.Run(args, Console.Out, Console.Error);
