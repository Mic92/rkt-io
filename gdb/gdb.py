# To use, add source /path/to/gdb.py to your $HOME/.gdbinit file.

import gdb
import atexit
import os
import re
import subprocess
import tempfile
import textwrap as tw
from typing import Optional, List
from collections import defaultdict
from dataclasses import dataclass

def add_symbol_file(filename, baseaddr):
    sections = []
    textaddr = '0'

    p = subprocess.Popen(["readelf", "-SW", filename], stdout=subprocess.PIPE)

    for line in p.stdout.readlines():
        line = line.decode("utf-8").strip()
        if not line.startswith('[') or line.startswith('[Nr]'):
            continue

        line = re.sub(r'\[ *(\d+)\]', '\\1', line)
        sec = dict(zip(['nr', 'name', 'type', 'addr'], line.split()))

        if sec['nr'] == '0':
            continue

        if sec['name'] == '.text':
            textaddr = sec['addr']
        elif int(sec['addr'], 16) != 0:
            sections.append(sec)

    cmd = "add-symbol-file %s 0x%08x" % (filename, int(textaddr, 16) + baseaddr)

    for s in sections:
        addr = int(s['addr'], 16)
        if s['name'] == '.text' or addr == 0:
            continue

        cmd += " -s %s 0x%x" % (s['name'], int(baseaddr + addr))

    gdb.execute(cmd)


class StarterExecBreakpoint(gdb.Breakpoint):
    STARTER_HAS_LOADED = '__gdb_hook_starter_ready'

    def __init__(self):
        super(StarterExecBreakpoint, self).__init__(self.STARTER_HAS_LOADED, internal=True)
        self.inited = False

    def stop(self):
        gdb.write('__gdb_hook_starter_ready.\n')
        base_addr = gdb.parse_and_eval('conf->base')
        in_hw_mode = gdb.parse_and_eval('conf->mode == SGXLKL_HW_MODE')
        if in_hw_mode:
            gdb.write('Running on hardware... skipping simulation load.\n')
        else:
            libsgxlkl = gdb.execute('printf "%s", libsgxlkl_path', to_string=True)
            gdb.write('Loading symbols for %s at base 0x%x...\n' % (
                libsgxlkl, int(base_addr)))
            add_symbol_file(libsgxlkl, int(base_addr))

        gdb.write('Looking up __gdb_load_debug_symbols_alive symbol.\n');
        if not self.inited and gdb.lookup_global_symbol("__gdb_load_debug_symbols_alive"):
            gdb.write('Enabled loading in-enclave debug symbols\n')
            gdb.execute('set __gdb_load_debug_symbols_alive = 1')
            gdb.write('set __gdb_load_debug_symbols_alive = 1\n')
            self.inited = True
            LoadLibraryBreakpoint()
            LoadLibraryFromFileBreakpoint()

        return False


class LoadLibraryBreakpoint(gdb.Breakpoint):
    LDSO_LOAD_LIBRARY = '__gdb_hook_load_debug_symbols'

    def __init__(self):
        super(LoadLibraryBreakpoint, self).__init__(self.LDSO_LOAD_LIBRARY, internal=True)

    def stop(self):
        # dump symbols out to disk
        uintptr_t = gdb.lookup_type('uintptr_t')
        ssize_t = gdb.lookup_type('ssize_t')

        mem_loc = int(gdb.parse_and_eval('symmem').cast(uintptr_t))
        mem_sz = int(gdb.parse_and_eval('symsz').cast(ssize_t))
        memvw = gdb.selected_inferior().read_memory(mem_loc, mem_sz)

        # work out where new library is loaded
        base_addr = int(gdb.parse_and_eval('dso->base').cast(uintptr_t))
        fn = None
        with tempfile.NamedTemporaryFile(suffix='.so', delete=False) as f:
            f.write(memvw)
            fn = f.name

        gdb.write('Loading symbols at base 0x%x...\n' % (int(base_addr)))
        add_symbol_file(fn, int(base_addr))

        atexit.register(os.unlink, fn)
        return False


class LoadLibraryFromFileBreakpoint(gdb.Breakpoint):
    LDSO_LOAD_LIBRARY_FROM_FILE = '__gdb_hook_load_debug_symbols_from_file'

    def __init__(self):
        super(LoadLibraryFromFileBreakpoint, self).__init__(self.LDSO_LOAD_LIBRARY_FROM_FILE, internal=True)

    def stop(self):
        uintptr_t = gdb.lookup_type('uintptr_t')
        libpath = gdb.execute('printf "%s", libpath', to_string=True)
        base_addr = int(gdb.parse_and_eval('dso->base').cast(uintptr_t))

        gdb.write('Loading symbols at base 0x%x...\n' % (int(base_addr)))
        add_symbol_file(libpath, int(base_addr))

        return False


def get_lthread_backtrace(lt_addr: str,
                          btdepth: str,
                          capture: bool = False
) -> Optional[str]:
    old_fp = gdb.execute('p/x $rbp', to_string=True).split('=')[1].strip()
    old_sp = gdb.execute('p/x $rsp', to_string=True).split('=')[1].strip()
    old_ip = gdb.execute('p/x $rip', to_string=True).split('=')[1].strip()

    gdb.execute('set $rbp = ((struct lthread *)%s)->ctx.ebp' % lt_addr)
    gdb.execute('set $rsp = ((struct lthread *)%s)->ctx.esp' % lt_addr)
    gdb.execute('set $rip = ((struct lthread *)%s)->ctx.eip' % lt_addr)

    output = gdb.execute('bt %s' % btdepth, to_string=capture)

    # Restore registers
    gdb.execute('set $rbp = %s' % old_fp)
    gdb.execute('set $rsp = %s' % old_sp)
    gdb.execute('set $rip = %s' % old_ip)

    return output


class LthreadBacktrace(gdb.Command):
    """
        Print backtrace for an lthread
        Param 1: Address of lthread
        Param 2: Backtrace depth (optional)
    """
    def __init__(self):
        super(LthreadBacktrace, self).__init__("lthread-bt", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        argv = gdb.string_to_argv(arg)
        if not argv:
            gdb.write('No lthread address provided. Usage: lthread-bt <addr> [<btdepth>]\n')
            gdb.flush()
            return False
        lt_addr = argv[0]
        if len(argv) > 1:
            btdepth = argv[1]
        else:
            btdepth = ""

        get_lthread_backtrace(lt_addr, btdepth)

        return False


class LthreadStats(gdb.Command):
    """
        Prints the number of lthreads in the futex, scheduler, and syscall queues.
    """
    def __init__(self):
        super(LthreadStats, self).__init__("lthread-stats", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        argv = gdb.string_to_argv(arg)
        if argv and len(argv) > 0:
            btdepth = argv[0]
        else:
            btdepth = ""

        schedq_lts = 0
        syscall_req_lts = 0
        syscall_ret_lts = 0
        fxq_lts = 0

        schedq_lts = self.count_queue_elements('__scheduler_queue')
        syscall_req_lts = self.count_queue_elements('__syscall_queue')
        syscall_ret_lts = self.count_queue_elements('__return_queue')

        fxq = gdb.execute('p/x futex_queues->slh_first', to_string=True).split('=')[1].strip()
        while(int(fxq, 16) != 0):
            fxq_lts = fxq_lts + 1;
            fxq = gdb.execute('p/x ((struct futex_q*)%s)->entries.sle_next'%fxq, to_string=True).split('=')[1].strip()

        waiting_total = schedq_lts + syscall_req_lts + syscall_ret_lts + fxq_lts

        gdb.write('Waiting lthreads:\n')
        gdb.write('  scheduler queue:       %s\n'%schedq_lts)
        gdb.write('  syscall request queue: %s\n'%syscall_req_lts)
        gdb.write('  syscall return queue:  %s\n'%syscall_ret_lts)
        gdb.write('  waiting for futex:     %s\n'%fxq_lts)
        gdb.write('  Total:                 %s\n'%waiting_total)
        gdb.flush()

        return False

    def count_queue_elements(self, queue):
        enqueue_pos = int(gdb.execute('p %s->enqueue_pos'%queue, to_string=True).split('=')[1].strip())
        dequeue_pos = int(gdb.execute('p %s->dequeue_pos'%queue, to_string=True).split('=')[1].strip())
        return enqueue_pos - dequeue_pos


class LogAllLts(gdb.Command):
    """
        Do a backtrace of all active lthreads.
        Param: Depth of backtrace (optional)
    """
    def __init__(self):
        super(LogAllLts, self).__init__("bt-lts", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        argv = gdb.string_to_argv(arg)
        if argv and len(argv) > 0:
            btdepth = argv[0]
        else:
            btdepth = ""

        ltq = gdb.execute('p/x __active_lthreads', to_string=True).split('=')[1].strip()

        no = 1
        while(int(ltq, 16) != 0):
            lt = gdb.execute('p/x ((struct lthread_queue*)%s)->lt'%ltq, to_string=True).split('=')[1].strip()
            lt_tid = gdb.execute('p/d ((struct lthread_queue*)%s)->lt->tid'%ltq, to_string=True).split('=')[1].strip()
            lt_name = gdb.execute('p/s ((struct lthread_queue*)%s)->lt->funcname'%ltq, to_string=True).split('=')[1].strip().split(',')[0]
            lt_cpu = gdb.execute('p/d ((struct lthread_queue*)%s)->lt->cpu'%ltq, to_string=True).split('=')[1].strip()
            gdb.write('#%3d Lthread: TID: %3s, Addr: %s, Name: %s, CPU: %s\n'%(no, lt_tid, lt, lt_name, lt_cpu))
            gdb.execute('lthread-bt %s %s'%(lt, btdepth))
            gdb.write('\n')
            gdb.flush()

            ltq = gdb.execute('p/x ((struct lthread_queue*)%s)->next'%ltq, to_string=True).split('=')[1].strip()
            no = no + 1

        return False

class LogAllLtsCsv(gdb.Command):
    """
        Do a backtrace of all active lthreads.
        Param: Depth of backtrace (optional)
    """
    def __init__(self) -> None:
        super(LogAllLtsCsv, self).__init__("bt-lts-csv", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty) -> bool:
        import csv

        argv = gdb.string_to_argv(arg)
        if argv and len(argv) > 0:
            btdepth = argv[0]
        else:
            btdepth = ""

        ltq = gdb.execute('p/x __active_lthreads', to_string=True).split('=')[1].strip()

        no = 1
        rows = []
        while(int(ltq, 16) != 0):
            lt = gdb.execute('p/x ((struct lthread_queue*)%s)->lt'%ltq, to_string=True).split('=')[1].strip()
            tid = gdb.execute('p/d ((struct lthread_queue*)%s)->lt->tid'%ltq, to_string=True).split('=')[1].strip()
            name = gdb.execute('p/s ((struct lthread_queue*)%s)->lt->funcname'%ltq, to_string=True).split('=')[1].strip().split(',')[0]
            cpu = gdb.execute('p/d ((struct lthread_queue*)%s)->lt->cpu'%ltq, to_string=True).split('=')[1].strip()
            bt = get_lthread_backtrace(lt, btdepth, capture=True)
            rows.append([lt, tid, name, cpu, bt])

            ltq = gdb.execute('p/x ((struct lthread_queue*)%s)->next'%ltq, to_string=True).split('=')[1].strip()
            no = no + 1

        dest = "/tmp/backtrace.csv"
        print(f"write to {dest}")
        with open(dest, "w") as f:
            writer = csv.writer(f)
            fields = ["thread", "tid", "name", "cpu", "backtrace"]
            writer.writerow(fields)
            for val in rows:
                writer.writerow(val)
        return False


@dataclass
class FxWaiter:
    key: str
    lt: str
    deadline: str
    backtrace: str


def get_fx_waiters(btdepth: str) -> List[FxWaiter]:
    waiters = []
    fxq = gdb.execute('p/x futex_queues->slh_first', to_string=True).split('=')[1].strip()
    while(int(fxq, 16) != 0):
        ft_lt = gdb.execute('p/x ((struct futex_q*)%s)->futex_lt'%fxq, to_string=True).split('=')[1].strip()
        ft_key = gdb.execute('p ((struct futex_q*)%s)->futex_key'%fxq, to_string=True).split('=')[1].strip()
        ft_deadline = gdb.execute('p ((struct futex_q*)%s)->futex_deadline'%fxq, to_string=True).split('=')[1].strip()
        ft_bt = gdb.execute('lthread-bt %s %s'%(ft_lt, btdepth), to_string=True)
        waiter = FxWaiter(key=ft_key, lt=ft_lt, deadline=ft_deadline, backtrace=ft_bt)
        waiters.append(waiter)
        fxq = gdb.execute('p/x ((struct futex_q*)%s)->entries.sle_next'%fxq, to_string=True).split('=')[1].strip()
    return waiters


class LogFxWaiters(gdb.Command):
    """
        Do a backtrace of all lthreads waiting on a futex
        Param: Depth of backtrace (optional)
    """
    def __init__(self):
        super(LogFxWaiters, self).__init__("bt-fxq", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        argv = gdb.string_to_argv(arg)
        if argv and len(argv) > 0:
            btdepth = argv[0]
        else:
            btdepth = ""
        waiters = get_fx_waiters(btdepth)
        for w in waiters:
            gdb.write('FX entry: key: %s, lt: %s, deadline: %s\n'%(w.key, w.lt, w.deadline))
            gdb.write(w.backtrace)
            gdb.write("\n")
        gdb.flush()

        return False

class LogFxWaitersCSV(gdb.Command):
    """
        Do a backtrace of all lthreads waiting on a futex
        Param: Depth of backtrace (optional)
    """
    def __init__(self):
        super(LogFxWaitersCSV, self).__init__("bt-fxq-csv", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        import csv
        argv = gdb.string_to_argv(arg)
        if argv and len(argv) > 0:
            btdepth = argv[0]
        else:
            btdepth = ""

        waiters = get_fx_waiters(btdepth)
        print(len(waiters))
        dest = "/tmp/waiters.csv"
        print(f"write to {dest}")
        with open(dest, "w") as f:
            writer = csv.writer(f)
            fields = ["key", "lt", "deadline", "backtrace"]
            writer.writerow(fields)
            for val in waiters:
                writer.writerow((val.key, val.lt, val.deadline, val.backtrace))

        return False

class LogSchedQueueTids(gdb.Command):
    """
        Print thread id of each lthread in scheduler queue.
    """
    def __init__(self):
        super(LogSchedQueueTids, self).__init__("schedq-tids", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):

        enqueue_pos = int(gdb.execute('p __scheduler_queue->enqueue_pos', to_string=True).split('=')[1].strip())
        dequeue_pos = int(gdb.execute('p __scheduler_queue->dequeue_pos', to_string=True).split('=')[1].strip())
        if (enqueue_pos < dequeue_pos): raise Exception("Logic error: %d < %d"%(enqueue_pos, dequeue_pos))

        buffer_mask = int(gdb.execute('p __scheduler_queue->buffer_mask', to_string=True).split('=')[1].strip())

        tids = []
        for i in range(dequeue_pos, enqueue_pos):
            gdb.write('p ((struct lthread*)__scheduler_queue->buffer[%d & %d].data)->tid\n'%(i, buffer_mask))
            tid = int(gdb.execute('p ((struct lthread*)__scheduler_queue->buffer[%d & %d].data)->tid'%(i, buffer_mask), to_string=True).split('=')[1].strip())
            tids.append(tid)

        gdb.write('\nScheduler queue lthreads:\n'+tw.fill(str(tids))+'\n')
        gdb.flush()


class LogSyscallBacktraces(gdb.Command):
    """
        Print backtraces for all lthreads waiting in the syscall queues.
        Param: Depth of backtrace (optional)
    """
    def __init__(self):
        super(LogSyscallBacktraces, self).__init__("bt-syscallqueues", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        argv = gdb.string_to_argv(arg)
        if argv and len(argv) > 0:
            btdepth = argv[0]
        else:
            btdepth = ""

        gdb.write('Lthreads in system call request queue:\n')
        self.print_bts_for_queue('__syscall_queue', btdepth)
        gdb.write('\nLthreads in system call return queue:\n')
        self.print_bts_for_queue('__return_queue', btdepth)

        return False

    def print_bts_for_queue(self, queue, btdepth):
        enqueue_pos = int(gdb.execute('p %s->enqueue_pos'%queue, to_string=True).split('=')[1].strip())
        dequeue_pos = int(gdb.execute('p %s->dequeue_pos'%queue, to_string=True).split('=')[1].strip())
        if (enqueue_pos < dequeue_pos): raise Exception("Logic error: %d < %d"%(enqueue_pos, dequeue_pos))

        buffer_mask = int(gdb.execute('p %s->buffer_mask'%queue, to_string=True).split('=')[1].strip())

        for i in range(dequeue_pos, enqueue_pos):
            lt = gdb.execute('p/x slotlthreads[%s->buffer[%d & %d].data]'%(queue, i, buffer_mask), to_string=True).split('=')[1].strip()
            if(lt != '0x0'):
                tid = int(gdb.execute('p ((struct lthread*)%s)->tid'%lt, to_string=True).split('=')[1].strip())
                gdb.write('Lthread [tid=%d]\n'%tid)
                gdb.execute('lthread-bt %s %s'%(lt, btdepth))
                gdb.write('\n')
            else:
                gdb.write('Queue entry without associated lthread...\n')

        gdb.flush()


class LogSyscallTids(gdb.Command):
    """
        Print tids of lthreads in syscall and return queues.
    """
    def __init__(self):
        super(LogSyscallTids, self).__init__("syscall-tids", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        gdb.write('\nSlot tids:\n'+tw.fill(str(self.slot_tids())))
        gdb.write('\nSlot syscallnos:\n'+tw.fill(str(self.syscall_nos())))
        gdb.write('\nSyscall tids:\n'+tw.fill(str(self.queue_tids('syscall'))))
        gdb.write('\nReturn tids:\n'+tw.fill(str(self.queue_tids('return'))))
        gdb.flush()


    def slot_tids(self):
        maxsyscalls = int(gdb.execute('p maxsyscalls', to_string=True).split('=')[1].strip())
        slot_tids = {}
        for i in range(0, maxsyscalls):
            if int(gdb.execute('p (int)slotlthreads[%d]'%i, to_string=True).split('=')[1].strip()) != 0:
                tid = int(gdb.execute('p slotlthreads[%d]->tid'%i, to_string=True).split('=')[1].strip())
                slot_tids[i] = tid

        return slot_tids

    def queue_tids(self, queue):
        enqueue_pos = int(gdb.execute('p __%s_queue->enqueue_pos'%queue, to_string=True).split('=')[1].strip())
        dequeue_pos = int(gdb.execute('p __%s_queue->dequeue_pos'%queue, to_string=True).split('=')[1].strip())
        if (enqueue_pos < dequeue_pos): raise Exception("Logic error: %d < %d"%(enqueue_pos, dequeue_pos))

        buffer_mask = int(gdb.execute('p __%s_queue->buffer_mask'%queue, to_string=True).split('=')[1].strip())

        tids = []
        for i in range(dequeue_pos, enqueue_pos):
            slot = int(gdb.execute('p ((int)__%s_queue->buffer[%d & %d].data)'%(queue, i, buffer_mask), to_string=True).split('=')[1].strip())
            if int(gdb.execute('p (int)slotlthreads[%d]'%slot, to_string=True).split('=')[1].strip()) != 0:
                tid = int(gdb.execute('p slotlthreads[%d]->tid'%slot, to_string=True).split('=')[1].strip())
                tids.append(tid)
            else:
                gdb.write('\nNo lthread found for queue slot %d in slotlthreads\n'%slot)

        return tids

    def syscall_nos(self):
        maxsyscalls = int(gdb.execute('p maxsyscalls', to_string=True).split('=')[1].strip())
        slot_syscallnos = {}
        for i in range(0, maxsyscalls):
            if int(gdb.execute('p (int)slotlthreads[%d]'%i, to_string=True).split('=')[1].strip()) != 0:
                sno = int(gdb.execute('p S[%d].syscallno'%i, to_string=True).split('=')[1].strip())
                slot_syscallnos[i] = sno

        return slot_syscallnos


stacktrace_regex = re.compile(r"\[[0-9. ]+\]\s+[0-9a-f]+:\s+\[<([0-9a-f]+)>\]")
info_line = re.compile(r'Line (\d+) of "([^"]+)" starts at address 0x[0-9a-f]+ <([^>]+)>')


class BtLkl(gdb.Command):
    """
        Recovers backtrace via lkl's dump_stack function
    """
    def __init__(self):
        super(BtLkl, self).__init__("bt-lkl", gdb.COMMAND_USER)

    def parse_stack_trace(self):
        """
        Screen scrape lkl's output and get the line of every entry
        """
        out = subprocess.check_output(["tmux", "capture-pane", "-pS", "-1000"])
        frames = []
        found_call_trace = False
        for line in out.decode("utf-8").split("\n"):
            line = line.rstrip()
            if "Call Trace:" in line:
                found_call_trace = True
                frames = []
                continue
            if found_call_trace:
                match = stacktrace_regex.match(line)
                if not match:
                    found_call_trace = False
                    continue
                frames.append(int(match.group(1), 16))
        return frames

    def invoke(self, arg, from_tty):
        if os.environ.get("SGXLKL_VERBOSE", None) != "1":
            gdb.write("Environment variable SGXLKL_VERBOSE=1 is not set!\n")
            return
        gdb.execute('call dump_stack()')
        frames = self.parse_stack_trace()
        for i, frame in enumerate(frames):
             line = gdb.execute('info line *0x%x' % frame, to_string=True)
             match = info_line.match(line)
             if match:
                 symbol_offset = match.group(3)
                 filename = match.group(2)
                 line = match.group(1)
                 gdb.write("[%3d] %50s in %s:%s\n" % (i, symbol_offset, filename, line))
             else:
                 # better safe then sorry
                 gdb.write("[%3d] %s\n" %(i, line))
        gdb.flush()

class Hexyl(gdb.Command):
    """
       Run hexyl on provided symbol/address. Takes the number of bytes to print as an optional parameter
    """
    def __init__(self):
        super(Hexyl, self).__init__("hexyl", gdb.COMMAND_USER)
        self.long_int = gdb.lookup_type("long")

    def invoke(self, arg, from_tty):
        argv = gdb.string_to_argv(arg)
        count = 0x100
        if argv and len(argv) > 0:
            address = argv[0]
            if len(argv) > 1:
                count = argv[1]
        else:
            address = "$sp"

        with tempfile.NamedTemporaryFile(suffix='.bin', delete=True) as f:
            gdb.execute("dump binary memory %s %s (%s + %s)" % (f.name, address, address, count))

        return False


class Curpath(gdb.Command):
    """
Print absolute path of the current file.
"""
    def __init__(self):
        super().__init__('curpath', gdb.COMMAND_FILES)
    def invoke(self, argument, from_tty):
        gdb.write(gdb.selected_frame().find_sal().symtab.fullname() + os.linesep)

class CachedType:
    def __init__(self, name):
        self._type = None
        self._name = name

    def _new_objfile_handler(self, event):
        self._type = None
        gdb.events.new_objfile.disconnect(self._new_objfile_handler)

    def get_type(self):
        if self._type is None:
            self._type = gdb.lookup_type(self._name)
            if self._type is None:
                raise gdb.GdbError(
                    "cannot resolve type '{0}'".format(self._name))
            if hasattr(gdb, 'events') and hasattr(gdb.events, 'new_objfile'):
                gdb.events.new_objfile.connect(self._new_objfile_handler)
        return self._type


task_type = CachedType("struct task_struct")
thread_info_type = CachedType("struct thread_info")
long_type = CachedType("long")



def get_long_type():
    global long_type
    return long_type.get_type()


def offset_of(typeobj, field):
    element = gdb.Value(0).cast(typeobj)
    return int(str(element[field].address).split()[0], 16)


def container_of(ptr, typeobj, member):
    return (ptr.cast(get_long_type()) -
            offset_of(typeobj, member)).cast(typeobj)


def task_lists():
    task_ptr_type = task_type.get_type().pointer()
    init_task = gdb.parse_and_eval("init_task").address
    t = g = init_task

    while True:
        while True:
            yield t

            t = container_of(t['thread_group']['next'],
                             task_ptr_type, "thread_group")
            if t == g:
                break

        t = g = container_of(g['tasks']['next'],
                             task_ptr_type, "tasks")
        if t == init_task:
            return


def get_task_by_pid(pid):
    for task in task_lists():
        if int(task['pid']) == pid:
            return task
    return None


class LxTaskByPidFunc(gdb.Function):
    """Find Linux task by PID and return the task_struct variable.

$lx_task_by_pid(PID): Given PID, iterate over all tasks of the target and
return that task_struct variable which PID matches."""

    def __init__(self):
        super(LxTaskByPidFunc, self).__init__("lx_task_by_pid")

    def invoke(self, pid):
        task = get_task_by_pid(pid)
        if task:
            return task.dereference()
        else:
            raise gdb.GdbError("No task of PID " + str(pid))



class LxPs(gdb.Command):
    """Dump Linux tasks."""

    def __init__(self):
        super(LxPs, self).__init__("lx-ps", gdb.COMMAND_DATA)

    def invoke(self, arg, from_tty):
        for task in task_lists():
            # adapted from:
            #define task_thread_info(task)	((struct thread_info *)(task)->stack)
            thread_info = task["stack"].cast(thread_info_type.get_type().pointer())
            # f"{int(t['tid'])}"
            gdb.write("{address} {pid} 0x{tid:02x} {comm}\n".format(
                address=task,
                pid=task["pid"],
                tid=int(thread_info["tid"]),
                comm=task["comm"].string()))


       
if __name__ == '__main__':
    StarterExecBreakpoint()
    LthreadBacktrace()
    LthreadStats()
    LogAllLts()
    LogAllLtsCsv()
    LogFxWaiters()
    LogFxWaitersCSV()
    LogSchedQueueTids()
    LogSyscallBacktraces()
    LogSyscallTids()
    BtLkl()
    Hexyl()
    Curpath()
    LxTaskByPidFunc()
    LxPs()
