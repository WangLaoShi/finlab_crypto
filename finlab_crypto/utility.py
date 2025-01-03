from IPython.display import display, HTML, IFrame, clear_output
from itertools import compress, product
from collections.abc import Iterable
import matplotlib.pyplot as plt
import tqdm.notebook as tqdm
import ipywidgets as widgets
import vectorbt as vbt
import seaborn as sns
import pandas as pd
import numpy as np

from . import chart


def is_evalable(obj):
    # 检查对象是否可以被 eval 函数求值
    try:
        eval(str(obj))
        return True
    except:
        return False

def remove_pd_object(d):
    # 移除字典中值为 pandas 对象的项
    ret = {}
    for n, v in d.items():
        if ((not isinstance(v, pd.Series) and not isinstance(v, pd.DataFrame) and not callable(v) and is_evalable(v))
            or isinstance(v, str)):
            ret[n] = v
    return ret

def enumerate_variables(variables):
    # 枚举变量
    if not variables:
        return []

    enumeration_name = []
    enumeration_vars = []

    constant_d = {}

    for name, v in variables.items():
        if (isinstance(v, Iterable) and not isinstance(v, str)
            and not isinstance(v, pd.Series)
            and not isinstance(v, pd.DataFrame)):

            enumeration_name.append(name)
            enumeration_vars.append(v)
        else:
            constant_d[name] = v

    variable_enumerations = [dict(**dict(zip(enumeration_name, ps)), **constant_d)
                             for ps in list(product(*enumeration_vars))]

    return variable_enumerations

def enumerate_signal(ohlcv, strategy, variables):
    # 枚举信号
    entries = {}
    exits = {}
    fig = {}

    iteration = tqdm.tqdm(variables) if len(variables) > 1 else variables
    for v in iteration:
        strategy.set_parameters(v)
        results = strategy.func(ohlcv)

        v = remove_pd_object(v)

        entries[str(v)], exits[str(v)] = results[0], results[1]
        if len(results) >= 3:
            fig = results[2]

    entries = pd.DataFrame(entries)
    exits = pd.DataFrame(exits)

    # 设置列
    param_names = list(eval(entries.columns[0]).keys())
    arrays = ([entries.columns.map(lambda s: eval(s)[p]) for p in param_names])
    tuples = list(zip(*arrays))
    if tuples:
        columns = pd.MultiIndex.from_tuples(tuples, names=param_names)
        exits.columns = columns
        entries.columns = columns
    return entries, exits, fig

def stop_early(ohlcv, entries, exits, stop_vars, enumeration=True):
    # 提前停止
    if not stop_vars:
        return entries, exits

    # 检查停止变量
    length = -1
    stop_vars_set = {'sl_stop', 'ts_stop', 'tp_stop', 'sl_trail'}
    for s, slist in stop_vars.items():
        if s not in stop_vars_set:
            raise Exception(f'variable { s } is not one of the stop variables'
                             ': sl_stop, ts_stop, or tp_stop')
        if not isinstance(slist, Iterable):
            stop_vars[s] = [slist]

        if length == -1:
            length = len(stop_vars[s])

        if not enumeration and length != -1 and length != len(stop_vars[s]):
            raise Exception(f'lengths of the variables are not align: '
                    + str([len(stop_vars[s]) for s, slist in stop_vars.items()]))

    if enumeration:
        stop_vars = enumerate_variables(stop_vars)
        stop_vars = {key: [stop_vars[i][key] for i in range(len(stop_vars))] for key in stop_vars[0].keys()}

    # vbt 补丁: 将 ts_stop 改为 sl_trail
    if 'ts_stop' in stop_vars:
        ts_stop = stop_vars.pop('ts_stop')
        stop_vars['sl_trail'] = ts_stop

    sl_advstex = vbt.OHLCSTX.run(
        entries,
        ohlcv['open'],
        ohlcv['high'],
        ohlcv['low'],
        ohlcv['close'],
        **stop_vars
    )

    stop_exits = sl_advstex.exits

    nrepeat = int(len(stop_exits.columns) / len(entries.columns))
    if isinstance(stop_exits, pd.DataFrame):
        exits = exits.vbt.tile(nrepeat)
        entries = entries.vbt.tile(nrepeat)

    stop_exits = stop_exits.vbt | exits.values
    entries.columns = stop_exits.columns

    return entries, stop_exits

def plot_strategy(ohlcv,
                  entries,
                  exits,
                  portfolio,
                  fig_data,
                  pyechart_render_in_notebook=False,
                  html=None,
                  k_colors='world'):
    # 绘制策略,使用信号生成的投资组合
    txn = portfolio.positions.records
    txn['enter_time'] = ohlcv.iloc[txn.entry_idx].index.values
    txn['exit_time'] = ohlcv.iloc[txn.exit_idx].index.values

    # 绘制交易数据
    mark_lines = []
    for name, t in txn.iterrows():
        x = [str(t.enter_time), str(t.exit_time)]
        y = [t.entry_price, t.exit_price]
        name = t.loc[['entry_price', 'exit_price', 'return']].to_string()
        mark_lines.append((name, x, y))

    # 计算重叠图形
    overlaps = {}
    if 'overlaps' in fig_data:
        overlaps = fig_data['overlaps']

    # 计算子图形
    figures = {}
    if 'figures' in fig_data:
        figures = fig_data['figures']

    figures['entries & exits'] = pd.DataFrame(
        {'entries': entries.squeeze(), 'exits': exits.squeeze()})
    figures['performance'] = portfolio.cumulative_returns()

    c, info = chart.chart(ohlcv, overlaps=overlaps,
                          figures=figures, markerlines=mark_lines,
                          start_date=ohlcv.index[-min(1000, len(ohlcv))], end_date=ohlcv.index[-1], k_colors=k_colors)
    c.load_javascript()

    print("pyechart_render_in_notebook: ", pyechart_render_in_notebook)
    # 渲染图表
    if pyechart_render_in_notebook:
        c.render_notebook()
    else:
        if html is not None:
            c.render(html)

        c.render('render.html')

        from IPython.display import HTML, display
        display(HTML('render.html'))

    return c

def plot_combination(portfolio, cscv_result=None, metric='final_value'):
    # 绘制组合图
    sns.set()
    sns.set_style("whitegrid")

    fig, axes = plt.subplots(1, 2, figsize=(15, 4), sharey=False, sharex=False)
    fig.suptitle('Backtest Results')

    def heat_map(item, name1, name2, ax):
        if name1 != name2:
            sns.heatmap(item.reset_index().pivot(name1, name2)[0], cmap='magma_r', ax=ax)
        else:
            getattr(portfolio, item_name).groupby(name1).mean().plot(ax=ax)

    def best_n(portfolio, n):
        return getattr(portfolio, metric)().sort_values().tail(n).index

    best_10 = best_n(portfolio, 10)

    ax = (portfolio.cumulative_returns()[best_10] * 100).plot(ax=axes[0])
    ax.set(xlabel='time', ylabel='cumulative return (%)')

    axes[1].title.set_text('Drawdown (%)')
    for n, c in zip([5, 10, 20, 30], sns.color_palette("GnBu_d")):
        bests = best_n(portfolio, n)
        drawdown = portfolio.drawdown()[bests].min(axis=1)
        ax = drawdown.plot(linewidth=1, ax=axes[1])
    ax.set(xlabel='time', ylabel='drawdown (%)')

    plt.show()

    items = ['final_value', 'sharpe_ratio', 'sortino_ratio']
    fig, axes = plt.subplots(1, len(items), figsize=(15, 3),
                             sharey=False, sharex=False, constrained_layout=False)
    fig.subplots_adjust(top=0.75)
    fig.suptitle('Partial Differentiation')

    final_value = portfolio.final_value()
    if isinstance(final_value.index, pd.MultiIndex):
        index_names = final_value.index.names
    else:
        index_names = [final_value.index.name]

    for i, item in enumerate(items):
        results = {}
        for name in index_names:
            s = getattr(portfolio, item)()
            s = s.replace([np.inf, -np.inf], np.nan)
            results[name] = s.groupby(name).mean()

            if all(isinstance(idx, str) for idx in results[name].index):
                results[name] = results[name].reset_index(drop=True)

        results = pd.DataFrame(results)
        axes[i].title.set_text(item)
        results.plot(ax=axes[i])

    if cscv_result is None:
        return

    results = cscv_result

    fig, axes = plt.subplots(1, 3, figsize=(15, 5),
                             sharey=False, sharex=False, constrained_layout=False)
    fig.subplots_adjust(bottom=0.5)
    fig.suptitle('Combinatorially Symmetric Cross-validation')

    pbo_test = round(results['pbo_test'] * 100, 2)
    axes[0].title.set_text(f'Probability of overfitting: {pbo_test} %')
    axes[0].hist(x=[l for l in results['logits'] if l > -10000], bins='auto')
    axes[0].set_xlabel('Logits')
    axes[0].set_ylabel('Frequency')

    axes[1].title.set_text('Performance degradation')
    x, y = pd.DataFrame([results['R_n_star'], results['R_bar_n_star']]).dropna(axis=1).values
    sns.regplot(x=x, y=y, ax=axes[1])
    axes[1].set_xlabel('In-sample Performance')
    axes[1].set_ylabel('Out-of-sample Performance')

    axes[2].title.set_text('Stochastic dominance')
    if len(results['dom_df']) != 0: results['dom_df'].plot(ax=axes[2], secondary_y=['SD2'])
    axes[2].set_xlabel('Performance optimized vs non-optimized')
    axes[2].set_ylabel('Frequency')

def variable_visualization(portfolio):
    # 变量可视化
    param_names = portfolio.cumulative_returns().columns.names
    dropdown1 = widgets.Dropdown(
        options=param_names,
        value=param_names[0],
        description='axis 1:',
        disabled=False,
    )
    dropdown2 = widgets.Dropdown(
        options=param_names,
        value=param_names[0],
        description='axis 2:',
        disabled=False,
    )

    performance_metric = ['final_value',
        'calmar_ratio', 'max_drawdown', 'sharpe_ratio',
        'downside_risk', 'omega_ratio', 'conditional_value_at_risk']

    performance_dropdwon = widgets.Dropdown(
        options=performance_metric,
        value=performance_metric[0],
        description='performance',
        disabled=False,
    )

    out = widgets.Output()

    import matplotlib.pyplot as plt
    def update(v):
        name1 = dropdown1.value
        name2 = dropdown2.value
        performance = performance_dropdwon.value

        with out:
            if name1 != name2:
                df = (getattr(portfolio, performance)()
                      .reset_index().groupby([name1, name2]).mean()[performance]
                      .reset_index().pivot(name1, name2)[performance])

                df = df.replace([np.inf, -np.inf], np.nan)
                sns.heatmap(df)
            else:
                getattr(portfolio, performance)().groupby(name1).mean().plot()

            plt.show()

    dropdown1.observe(update, 'value')
    dropdown2.observe(update, 'value')
    performance_dropdwon.observe(update, 'value')
    drawdowns = widgets.VBox([performance_dropdwon,
                 widgets.HBox([dropdown1, dropdown2])])
    display(drawdowns)
    display(out)
    update(0)
