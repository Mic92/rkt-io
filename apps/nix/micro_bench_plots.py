import sys
from typing import Any, Dict, List

import pandas as pd
from plot import apply_hatch, catplot
import os

from graph_utils import apply_aliases, change_width, column_alias, apply_to_graphs


def preprocess_hdparm(df_col: pd.Series) -> Any:
    df_col = list(df_col.values)
    for i in range(len(df_col)):
        # import pdb; pdb.set_trace()
        temp = df_col[i].split(" ")
        if temp[1] == "kB/s":
            df_col[i] = float(temp[0]) / (1000 ** 2)
        elif temp[1] == "MB/s":
            df_col[i] = float(temp[0]) / (1000)
        elif temp[1] == "GB/s":
            df_col[i] = float(temp[0])

    return pd.Series(df_col)


def hdparm_zerocopy_plot(dir: str, graphs: List[Any]) -> None:
    df_all_on = pd.read_csv(
        os.path.join(os.path.realpath(dir), "hdparm-all-on-latest.tsv"), sep="\t"
    )

    df_zcopy_off = pd.read_csv(
        os.path.join(os.path.realpath(dir), "hdparm-zerocopy-off-latest.tsv"), sep="\t"
    )

    df_all_on = df_all_on.drop(columns=["system"])
    df_zcopy_off = df_zcopy_off.drop(columns=["system"])

    df_all_on["Timing buffered disk reads"] = preprocess_hdparm(
        df_all_on["Timing buffered disk reads"],
    )
    df_zcopy_off["Timing buffered disk reads"] = preprocess_hdparm(
        df_zcopy_off["Timing buffered disk reads"],
    )

    df_all_on["Timing buffer-cache reads"] = preprocess_hdparm(
        df_all_on["Timing buffer-cache reads"],
    )

    df_zcopy_off["Timing buffer-cache reads"] = preprocess_hdparm(
        df_zcopy_off["Timing buffer-cache reads"],
    )

    df_all_on = df_all_on.T.reset_index()
    df_zcopy_off = df_zcopy_off.T.reset_index()

    df_zcopy_off.columns = ["hdparm_kind", "hdparm-throughput"]
    df_all_on.columns = ["hdparm_kind", "hdparm-throughput"]

    df_all_on["feature_spdk"] = pd.Series(
        ["spdk-zerocopy"] * len(df_all_on.index), index=df_all_on.index
    )
    df_zcopy_off["feature_spdk"] = pd.Series(
        ["spdk-copy"] * len(df_zcopy_off.index), index=df_zcopy_off.index
    )

    plot_df = pd.concat([df_all_on, df_zcopy_off], axis=0)
    groups = len(set(list(plot_df["feature_spdk"].values)))

    g = catplot(
        data=apply_aliases(plot_df),
        x=column_alias("feature_spdk"),
        y=column_alias("hdparm-throughput"),
        kind="bar",
        height=2.5,
        legend=False,
        hue=column_alias("hdparm_kind"),
        palette=["grey", "black"],
    )

    # apply_hatch(groups, g, True)
    # change_width(g.ax, 0.25)
    # g.ax.set_xlabel("")

    # g.ax.set_xticklabels(g.ax.get_xmajorticklabels(), fontsize=6)
    # g.ax.set_yticklabels(g.ax.get_ymajorticklabels(), fontsize=6)
    apply_to_graphs(g.ax, True, 2)

    graphs.append(g)


def network_bs_plot(dir: str, graphs: List[Any]) -> None:
    df = pd.read_csv(
        os.path.join(os.path.realpath(dir), "network-test-bs-latest.tsv"), sep="\t"
    )
    df["network-bs-throughput"] = 1024 / df["time"]
    # df["batch_size"] = df["batch_size"].apply(lambda x: str(x)+"KiB")

    g = catplot(
        data=apply_aliases(df),
        x=column_alias("batch_size"),
        y=column_alias("network-bs-throughput"),
        kind="bar",
        height=2.5,
        legend=False,
        color="black",
        palette=None,
    )

    # change_width(g.ax, 0.25)
    # # g.ax.set_xlabel('')
    # g.ax.set_xticklabels(g.ax.get_xmajorticklabels(), fontsize=6)
    # g.ax.set_yticklabels(g.ax.get_ymajorticklabels(), fontsize=6)
    apply_to_graphs(g.ax, False, -1)

    graphs.append(g)


def storage_bs_plot(dir: str, graphs: List[Any]) -> None:
    df = pd.read_csv(
        os.path.join(os.path.realpath(dir), "simpleio-unenc.tsv"), sep="\t"
    )
    df["storage-bs-throughput"] = (10 * 1024) / df["time"]

    g = catplot(
        data=apply_aliases(df),
        x=column_alias("batch-size"),
        y=column_alias("storage-bs-throughput"),
        kind="bar",
        height=2.5,
        legend=False,
        color="black",
        palette=None,
    )

    # change_width(g.ax, 0.25)
    # # g.ax.set_xlabel('')
    # g.ax.set_xticklabels(g.ax.get_xmajorticklabels(), fontsize=6)
    # g.ax.set_yticklabels(g.ax.get_ymajorticklabels(), fontsize=6)

    apply_to_graphs(g.ax, False, -1)

    graphs.append(g)


def smp_plot(dir: str, graphs: List[Any]) -> None:
    df = pd.read_csv(
        os.path.join(os.path.realpath(dir), "smp-latest.tsv"), sep="\t"
    )
    df = pd.melt(df,
                 id_vars=['cores', 'job'],
                 value_vars=['read-bw', 'write-bw'],
                 var_name="operation",
                 value_name="disk-throughput")
    df = df.groupby(["cores", "operation"]).sum().reset_index()

    df["disk-throughput"] /= 1024
    g = catplot(
        data=apply_aliases(df),
        x=column_alias("cores"),
        y=column_alias("disk-throughput"),
        hue=column_alias("operation"),
        kind="bar",
        height=2.5,
        legend=False,
        palette=["grey", "black"],
    )

    # change_width(g.ax, 0.25)
    # # g.ax.set_xlabel('')
    # g.ax.set_xticklabels(g.ax.get_xmajorticklabels(), fontsize=6)
    # g.ax.set_yticklabels(g.ax.get_ymajorticklabels(), fontsize=6)
    # g.ax.legend(loc='best', fontsize='small')
    apply_to_graphs(g.ax, True, 2, 0.285)

    graphs.append(g)


def read_iperf(path: str, type: str) -> pd.DataFrame:
    df = pd.read_csv(path, sep="\t")
    df = df[df["direction"] == "send"]
    df["iperf-throughput"] = df["bytes"] / df["seconds"] * 8 / 1e9
    return df.assign(type=type)


def network_optimization_plot(dir: str, graphs: List[Any]) -> None:
    df_all = read_iperf(os.path.join(os.path.realpath(dir), "iperf-all-on-latest.tsv"), "offloads+\nzerocopy")

    df_offload = read_iperf(os.path.join(os.path.realpath(dir), "iperf-offload_off-latest.tsv"), "no offloads")

    df_zcopy = read_iperf(os.path.join(os.path.realpath(dir), "iperf-zerocopy_off-latest.tsv"), "no zerocopy")
    df = pd.concat([df_all, df_offload, df_zcopy])
    g = catplot(
        data=apply_aliases(df),
        x=column_alias("type"),
        y=column_alias("iperf-throughput"),
        kind="bar",
        height=2.5,
        # aspect=1.2,
        color="black",
        palette=None,
    )
    apply_to_graphs(g.ax, False, -1, 0.18)
    
    graphs.append(g)


def aesni_plot(dir: str, graphs: List[Any]) -> None:
    df = pd.read_csv(
        os.path.join(os.path.realpath(dir), "aesni-latest.tsv"), sep="\t"
    )
    df = df.assign(aesnithroughput=df.bytes / df.time / 1024 / 1024)
    g = catplot(
        data=apply_aliases(df),
        x=column_alias("type"),
        y=column_alias("aesnithroughput"),
        kind="bar",
        height=2.5,
        aspect=1.2,
        color="black",
        palette=None
    )
    apply_to_graphs(g.ax, False, -1, 0.1)
    g.ax.set_ylabel(g.ax.get_ylabel(), size=8)
    g.ax.set_xticklabels(g.ax.get_xticklabels(), size=8)
    g.ax.set_yticklabels(g.ax.get_yticklabels(), size=8)
    graphs.append(g)


def spdk_zerocopy_plot(dir: str, graphs: List[Any]) -> None:
    df = pd.read_csv(
        os.path.join(os.path.realpath(dir), "spdk-zerocopy-latest.tsv"), sep="\t"
    )
    df = df.assign(aesnithroughput=df.bytes / df.time / 1024 / 1024)
    g = catplot(
        data=apply_aliases(df),
        x=column_alias("type"),
        y=column_alias("aesnithroughput"),
        kind="bar",
        height=2.5,
        aspect=1.2,
    )
    change_width(g.ax, 0.25)
    g.ax.set_xlabel('')
    graphs.append(g)


def main() -> None:
    if len(sys.argv) < 1:
        sys.exit(1)

    graphs: List[Any] = []
    graph_names = []

    plot_func = {
        # disabled for now
        #"network_bs": network_bs_plot,
        #"storage_bs": storage_bs_plot,
        # "spdk_zerocopy": spdk_zerocopy_plot,
        "smp": smp_plot,
        "aesni": aesni_plot,
        "network_optimization": network_optimization_plot
    }

    for name, pf in plot_func.items():
        pf(sys.argv[1], graphs)
        graph_names.append(name)

    for i in range(len(graphs)):
        name = f"{graph_names[i]}.pdf"
        print(name)
        graphs[i].savefig(name)


if __name__ == "__main__":
    main()
