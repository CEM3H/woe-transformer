# AUTOGENERATED! DO NOT EDIT! File to edit: 0. dummy for funcs.ipynb (unless otherwise specified).

__all__ = ['readSQL', 'checkCustomer', 'checkApplication', 'checkMultCustOrAppl', 'compareSeries',
           'getLongDates', 'calcOpenCloseDiff', 'calcPeriods', 'joinPeriods', 'extractSubPmtStr',
           'extractSubPmtStrApply', 'getWorstStatus', 'calcWorstStatus']

"""
TODO:
1. Отрефакторить функции на иерархической кластеризации
"""

# Comes from 1. Superscore_Zenit_Features.ipynb, cell
import sys
sys.path.insert(0, r'K:\ДМиРТ\Управление моделирования\#Zenit.ai\zenitai-lib')
import typing
import numpy as np
import pandas as pd
import scipy.stats as sts
import datetime
import matplotlib.pyplot as plt
import pyodbc
from IPython.display import display
import woeTransformer_beta as woe
import re
import json

from collections import defaultdict
from catboost import Pool, CatBoostClassifier
from sklearn.model_selection import train_test_split, StratifiedKFold, TimeSeriesSplit, GridSearchCV
from sklearn.metrics import roc_auc_score, roc_curve
from sklearn.pipeline import Pipeline
from scipy.cluster import hierarchy
from sklearn.linear_model import LogisticRegression

from woeTransformer_class import WoeTransformer

is_notebook = sys.argv[-1].endswith('json')
if is_notebook:
    from tqdm.notebook import tqdm
else:
    from tqdm import tqdm


pd.options.mode.chained_assignment = None
cur_date = datetime.datetime.now().date()


# Comes from 1. Superscore_Zenit_Features.ipynb, cell
def readSQL(path, **kwargs):
    df = pd.read_csv(path, encoding='utf-8', sep=';', dtype='object', **kwargs)
    print(df.shape)
    return df

# Comes from 1. Superscore_Zenit_Features.ipynb, cell
def checkCustomer(df, cl_id, calc_diff=False, date_col='date', diff_row=-1):
    t = df[df['dr_customer_id'] == cl_id]
    if calc_diff:
        t = t.sort_values(date_col)
        t['month_passed'] = np.round(((t[date_col].iloc[diff_row] - t[date_col]).dt.days / 30.4375), 2)
        t['years_passed'] = np.round(((t[date_col].iloc[diff_row] - t[date_col]).dt.days / 365.25), 2)
    print(f'Subsample shape is {t.shape}')
    return t

# Comes from 1. Superscore_Zenit_Features.ipynb, cell
def checkApplication(df, appl_id, calc_diff=False, date_col='date', diff_row=-1):
    t = df[df['appl_id_khd'] == appl_id]
    if calc_diff:
        t = t.sort_values(date_col)
        t['month_passed'] = np.round(((t[date_col].iloc[diff_row] - t[date_col]).dt.days / 30.4375), 2)
        t['years_passed'] = np.round(((t[date_col].iloc[diff_row] - t[date_col]).dt.days / 365.25), 2)
    print(f'Subsample shape is {t.shape}')
    return t

# Comes from 1. Superscore_Zenit_Features.ipynb, cell
def checkMultCustOrAppl(df, id_list, mode, calc_diff=False, date_col='date', diff_row=-1):
    funcs = {'customer':checkCustomer,
             'application':checkApplication}
    res = pd.DataFrame()
    for i in id_list:
        res = res.append(funcs.get(mode)(df, i, calc_diff, date_col, diff_row))
    return res



def getSameFeats(feat, source, suffix=r'_all|_closed|_notnull|_0|_act[^ive]+|_act\b'):
    """
    WARNING! функция завязана на определенные названия датасетов и специфику задачи
    Поиск фичей в выборке Зенит, похожих по написанию с указанной фичей

    source - pd.DataFrame.columns

    Возвращает список столбцов датасета `zenit_data`
    """

    feat = re.sub('UBRR_', '', feat).lower()
    idx = source.str.replace(suffix, '').str.lower() == feat
    res = list(source[idx].values)
    return res


# %% ExecuteTime={"start_time": "2020-04-23T06:35:50.606160Z", "end_time": "2020-04-23T06:35:50.616186Z"}
def getSameFeatsReversed(feat, source, suffix=r'_all|_closed|_notnull|_0|_act[^ive]+|_act\b'):
    """
    WARNING! функция завязана на определенные названия датасетов и специфику задачи
    Поиск фичей в выборке УБРР, похожих по написанию с указанной фичей

    source - pd.DataFrame.columns

    Возвращает список столбцов датасета `ubrr_data`
    """
    feat = re.sub(suffix, '', feat).lower()
    idx = source.str.replace('UBRR_', '').str.lower() == feat
    res = list(source[idx].values)
    return res



# Comes from 1. Superscore_Zenit_Features.ipynb, cell
def compareSeries(s1, s2, ret_index=False):
    '''Сравнивает два объекта pd.Series на предмет наличия совпадающих
    элементов
    Вход:
        - s1, s2 : pd.Series, объекты для сравнения
        - ret_index : bool, модификатор вывода результатов
                        * True - возвращаются только
    Выход:
        - общие элементы из s1
        - общие элементы из s2
        - элементы, уникальные для s1
        - элементы, уникальные для s2
    '''
    assert type(s1) == pd.Series
    assert type(s2) == pd.Series

    s1_common_elems = s1[s1.isin(s2)]
    s2_common_elems = s2[s2.isin(s1)]
    s1_only = s1[~s1.isin(s2)]
    s2_only = s2[~s2.isin(s1)]

    return s1_common_elems, s2_common_elems, s1_only, s2_only

# Comes from 1. Superscore_Zenit_Features.ipynb, cell
def getLongDates(df, target_ids, credit_type,
                 drop_endless_сс=False, fill_enddt=False):
    ''' df - данные по кредитам из базы
        target_ids - список целевых клиентов
        credit_type - срочные кредиты или все кредиты (срочные+кредитные линии)
        drop_endless_сс - удалять ли кредитки без дат закрытия
        fill_enddt - заполнять пустые даты окончания или удалять записи с ними
    '''
    assert credit_type in ('sroch', 'all')

    # Выборка данных для целевых клиентов
    res = df.loc[df['dr_customer_id'].isin(target_ids)].copy()

    # Удалять ли кредитки без срока окончания
    if drop_endless_сс:
        idx_to_drop = res[(res['date_close'].isna()) & (res['date_end'].isna())].index
        res.drop(idx_to_drop, inplace=True)

    # Исключать ли кредитные линии
    if credit_type == 'sroch':
        res = res[res['credit_line_flag']==0]

    # Составление "длинного" списка дат для каждого клиента
    res = res.melt(id_vars='dr_customer_id',
                  value_vars=['date_open',  'date_end'],
                  value_name='date', var_name='diff')

    # Заполнить пустые факт. даты окончания или удалить
    if fill_enddt:
        res['date'] = res['date'].fillna(pd.Timestamp(cur_date))
    else:
        res = res[res['date'].notna()]
    # Присвоение маркеров датам открытия и закрытия
    res['diff'] = res['diff'].replace({'date_open':1,  'date_end':-1}).astype(int)
    res.sort_values(['dr_customer_id', 'date'], inplace=True)

    return res[['dr_customer_id', 'date', 'diff']]

# print(getLongDates(cred_data, trg_customers, 'all').shape)
# print(getLongDates(cred_data, trg_customers, 'sroch').shape)
# print(getLongDates(cred_data, trg_customers, 'all', True).shape)
# print(getLongDates(cred_data, trg_customers, 'sroch', True).shape)
# print(getLongDates(cred_data, trg_customers, 'all', True, True).shape)
# print(getLongDates(cred_data, trg_customers, 'sroch', True, True).shape)

# Comes from 1. Superscore_Zenit_Features.ipynb, cell
def calcOpenCloseDiff(target_df, source_df, credit_type='sroch',years=[0.5, 1, 3, 5, 30],):
    '''credit_type - срочные кредиты или лимиты
    '''
    def getTxtPeriod(x):
        '''хелпер для генерации строк из периодов, возвращает кортеж из периодов и тектосых представлений'''
        return zip(x, [f'{i}Y' if i >= 1 else f'{int(i*12)}M' for i in x])

    res = source_df.copy()
    # Соединение целевой выборки и данных о контрактах
    res = (pd.concat([res, target_df], axis=0, sort=False)
             .groupby(['dr_customer_id', 'date']).sum()
             .reset_index()
          )
    res.loc[:, ['target', 'diff', 'appl_id_khd']] = res.loc[:, ['target', 'diff', 'appl_id_khd']].fillna(0).astype(int)

    # Расчет фичей по заданным окнам (periods)
    agg_res = pd.DataFrame()
    for p, t in getTxtPeriod(years):
        d = str(int(365.25*p))
        agg_res[f'NUM_DIFF_{credit_type}_{t}'] =    (res
                                                    .set_index('date')
                                                    .groupby(['dr_customer_id'])['diff']
                                                    .rolling(d+'d', closed='left')
                                                    .sum()
                                                     )

    return agg_res.fillna(0).astype(int).reset_index()

# Comes from 1. Superscore_Zenit_Features.ipynb, cell
def calcPeriods(x, stepwise_display=False):
    df = x.copy()
    df['running'] = df.groupby('dr_customer_id')['diff'].cumsum()
    df['new_win'] = (df['running'].eq(1) & df['diff'].eq(1)).astype(int)
    df['win_num'] = df.groupby('dr_customer_id')['new_win'].cumsum()
    df['date'] = df['date'].fillna(pd.Timestamp(cur_date))
    if stepwise_display:
        display(x)
        display(df)
    df = (df.groupby(['dr_customer_id', 'win_num'])['date']
            .agg(['min', 'max'])).reset_index()
    return df
# calcPeriods(tmp_data, False)

# Comes from 1. Superscore_Zenit_Features.ipynb, cell
def joinPeriods(x, df, stepwise_display=False):
    # Слияние целевой выборки и непрерывных периодов
    df_ = x.merge(df, how='left', on='dr_customer_id')
    # Удаление периодов, начавшихся после ретро-даты
    df_ = df_[df_['date'] > df_['min']]
    # Расчет количества дней в зависимости от даты окончания периода
    df_.loc[df_['date'] >= df_['max'], 'days'] = df_['max'] - df_['min']
    df_.loc[df_['date'] < df_['max'], 'days'] = df_['date'] - df_['min']
    if stepwise_display: display(df_)

    df_ = df_.drop_duplicates(['dr_customer_id','date', 'win_num'])
    df_ = df_.groupby(['dr_customer_id', 'date'])['days'].sum().reset_index()
    df_ = x.merge(df_, how='left', on=['dr_customer_id','date'])
    df_['SUM_CH'] = df_['days'].dt.days / 30.4375

    return df_.drop('days', axis=1)
# joinPeriods(trg_data, periods_data, 0 )

# Comes from 1. Superscore_Zenit_Features.ipynb, cell
# %%timeit
def extractSubPmtStr(df, pmtstr, pmtstr_enddt, retro_dt, depth=12):
    ''' Для использования как stand-alone функции
    '''
    assert df[pmtstr_enddt].dtype == 'datetime64[ns]'
    assert df[retro_dt].dtype == 'datetime64[ns]'

    df_ = df[[pmtstr, pmtstr_enddt, retro_dt]].copy()

    # Очистка дат от времени
    df_[pmtstr_enddt] = df_[pmtstr_enddt].dt.normalize()
    df_[retro_dt] = df_[retro_dt].dt.normalize()

    # Разница в месяцах между ретро-датой и последней датой платежной строки
    a = np.floor((df_[retro_dt] - df_[pmtstr_enddt]).dt.days / 30.4375)

    # Создание вектора с целевой длиной подстроки
    df_['res'] = pd.Series(np.nan, index=df_.index)
    df_.loc[depth - a > 0, 'res'] = (depth - a)
    df_['res'] = df_['res'].fillna(0).astype(int)

    # Выделение подстроки
    res = df_.apply(lambda x: x[pmtstr][:x['res']], axis=1)
    return res

# Comes from 1. Superscore_Zenit_Features.ipynb, cell
# %%timeit
def extractSubPmtStrApply(row, pmtstr, pmtstr_enddt, retro_dt, depth=12):
    ''' Для использования из-под метода .apply
    '''
    # Разница в месяцах между ретро-датой и последней датой платежной строки
    a = np.floor((row[retro_dt] - row[pmtstr_enddt]).days / 30.4375)
    # Расчет целевой длиной подстроки
    res = (depth - a) if depth - a > 0 else 0
    # Выделение подстроки
    res = row[pmtstr][:int(res)]
    return a

# Comes from 1. Superscore_Zenit_Features.ipynb, cell
def getWorstStatus(x):
    ''' Функция для выбора наихудшего статуса из платежной строки
    можно применять в методе .apply
    '''
    x = [i for i in x if i != 'X']
    if x:
        return np.float(sorted(list(map(lambda x:'1.5' if x == 'A' else x , x)))[-1])
    else:
        return np.float(-1)
# getWorstStatus('1111111111AX'), getWorstStatus('')

# Comes from 1. Superscore_Zenit_Features.ipynb, cell
def calcWorstStatus(df, periods=[1, 3, 6, 12, 999]):
    res_ = pd.DataFrame({'appl_id_khd':df['appl_id_khd']})
    for i in periods:
        res_[f'OKB_WORST_STATUS_{i}'] = (extractSubPmtStr(df,
                                                      pmtstr='pmtstring84m',
                                                      pmtstr_enddt='pmtstringstart',
                                                      retro_dt='date',
                                                      depth=i)
                                     .apply(getWorstStatus)
                                    )
    res_ = res_.groupby('appl_id_khd').max().reset_index()

    return res_

def help_input(prompt):
    i = False
    ans = None
    a = {'y':True,
         'n':False}
    while i is False:
        ans = (a.get(input(prompt), 'need "y" or "n"'))
        if isinstance(ans, bool):
            return ans
        else:
            print(ans)


# %% ExecuteTime={"start_time": "2020-04-23T06:35:51.052474Z", "end_time": "2020-04-23T06:35:51.074564Z"} scrolled=true
def checkFeatStats(df, feat, val_counts=False):
    """ Расчет описательных статистик признака
    """
    print('Кол-во наблюдений:', len(df))
    print('Кoл-во пустых значений:', df[feat].isna().sum())

    d = {'count': len(df),
         'count_na': df[feat].isna().sum(),
         'count_unq_values': df[feat].nunique(),
         'min': df[feat].min(),
         'mean': df[feat].mean(),
         'median': df[feat].median(),
         'max': df[feat].max(),}
    if val_counts:
        val_count = df[feat].value_counts()
        display(val_count.reset_index())


    return pd.DataFrame.from_dict(d, orient='index',)


def trainWoe(df, feat, target, limit_rows=False, **kwargs):
    """ Обертка для WOE-преобразования
    """
    limit_rows = limit_rows if limit_rows else len(df)

    return woe.woeTransformer(df[feat].iloc[:limit_rows].fillna('пусто'),
                              df[target].iloc[:limit_rows],
                              **kwargs)


def stylerFloat(df, format_='{:,.1%}'):
    ''' Выводит датафрейм, форматируя числовые значения
    '''
    with pd.option_context('display.float_format', format_.format):
        display(df)

def splitTrainTestValid(df, target: str,
                        test_size=0.3, val_size=0.3,
                        verbose=False,
                        **kwargs):
    '''
    Разбиение выборки на обучающую, валидационную и тестовую c сохранением доли таргета
    kwargs - аргументы для train_test_split из scikit-learn
    Возвращает: X_train, X_val, X_test, y_train, y_val, y_test
    '''
#     kwargs.update({'stratify': df[target]})
    if kwargs.get('shuffle', True) is True:
        kwargs.update({'stratify': df[target]})
    else:
        kwargs.update({'stratify': None})

    # Выделение тестовой выборки
    y_data = df[target]
    X_data, X_test, y_data, y_test = train_test_split(df.drop(target, axis=1), df[target],
                                                      test_size=test_size,
                                                      **kwargs)
    # Выделение обучающей и валидационной выборок
    if kwargs.get('shuffle', True) is True:
        kwargs.update({'stratify': y_data})

    X_train, X_val, y_train, y_val = train_test_split(X_data, y_data,
                                                      test_size=val_size,
                                                      **kwargs)
    if verbose:
        print('train:', y_train.count(), y_train.sum(), y_train.mean(), sep='\t')
        print('valid.:', y_val.count(), y_val.sum(), y_val.mean(), sep='\t')
        print('test:', y_test.count(), y_test.sum(), y_test.mean(), sep='\t')

    return  [X_train, X_val, X_test, y_train, y_val, y_test]

# %% ExecuteTime={"start_time": "2020-04-23T06:35:53.528531Z", "end_time": "2020-04-23T06:35:53.542596Z"} code_folding=[]
def calcGini(X_tr, X_valid, X_t,
             y_tr, y_valid, y_t,
             n_iter=3, predictors=None):
    ''' Рассчитывает однофакторный Gini с помощью Catboost
    '''
    OA = []
    gini_OA = []
    hit_rate_OA = []
    if isinstance(predictors, list) and len(predictors) > 0:
        cols = predictors
    else:
        cols = X_tr.columns
    a = (~(X_tr.nunique().sort_values() == 1))

    cols = a[a==True].index

    for i in tqdm(cols): # цикл по предикторам
        gini_i = []
        hit_rate_i = []

        X_tr_i = X_tr[[i]].copy()
        X_valid_i = X_valid[[i]].copy()
        X_t_i = X_t[[i]].copy()


        if X_tr[i].dtype == object:  # возможны проблемы, т.к. было <data[i]>.dtype
            cat_features=[i]
            X_tr_i = X_tr_i.fillna('nan').astype('str')
            X_valid_i = X_valid_i.fillna('nan').astype('str')
            X_t_i = X_t_i.fillna('nan').astype('str')

        else:
            cat_features=None

        train_pool = Pool(X_tr_i, y_tr, cat_features=cat_features)
        valid_pool = Pool(X_valid_i, y_valid, cat_features=cat_features)
        test_pool = Pool(X_t_i, y_t, cat_features=cat_features)

        cb_model = CatBoostClassifier().fit(train_pool, eval_set=valid_pool, verbose=False, use_best_model=True)

        for (X_i, y_i, pool_i) in  ([(X_tr, y_tr, train_pool),
                                        (X_valid, y_valid, valid_pool),
                                        (X_t, y_t, test_pool)]): # цикл по выборкам

            hit_rate_i.append(1-(X_i[i][X_i[i] == 0].count() + X_i.shape[0] - X_i[i].count()) / X_i.shape[0])

            Score = cb_model.predict_proba(pool_i)[:,1]
            AUC = roc_auc_score(y_i, Score)
            gini_i.append(np.abs(2 * AUC - 1))

        gini_OA.append(gini_i)
        hit_rate_OA.append(hit_rate_i)

    OA = pd.DataFrame()
    OA['predictor'] = cols
    OA[['gini_tr', 'gini_valid', 'gini_t']] = pd.DataFrame(gini_OA)

    OA['overfitting_1'] = OA['gini_tr'] - OA['gini_valid']
    OA['overfitting_2'] = OA['gini_tr'] - OA['gini_t']

    OA[['hit_rate_tr', 'hit_rate_valid', 'hit_rate_t']] = pd.DataFrame(hit_rate_OA)

    OA.sort_values('gini_tr', ascending=False, inplace=True) # сортировка по однофакторному Gini
    OA.reset_index(drop=True, inplace=True)

    return OA

def calcPSI(exp, act):
        exp = exp.value_counts(normalize=True).sort_index()
        act = act.value_counts(normalize=True).sort_index()

        df = pd.concat([exp, act], axis=1).fillna(0).reset_index()
        df.columns = ['group', 'expected', 'actual']
        df['PSI'] = ((df['actual'] - df['expected'])
                    * np.log((df['actual'] + 0.000001)/(df['expected'] + 0.000001)))
        return df





# %% ExecuteTime={"start_time": "2020-04-30T08:38:23.169126Z", "end_time": "2020-04-30T08:38:23.173141Z"}
def auc_to_gini(auc):
    return 2 * auc - 1


# %% ExecuteTime={"start_time": "2020-04-30T08:38:23.176145Z", "end_time": "2020-04-30T08:38:23.181310Z"}
def trainBaselineCatbost(X_train, X_valid, y_train, y_valid,
                         cat_features=None,
                         **kwargs):
    """
    Обучить Catboost с валидацией
    """
    train_pool = Pool(X_train, y_train, cat_features=cat_features)
    valid_pool = Pool(X_valid, y_valid, cat_features=cat_features)

    cb_model = CatBoostClassifier(od_type='IncToDec',
                                  od_pval=10**-3,
                                  eval_metric='AUC',
                                  **kwargs
                                  ).fit(train_pool, eval_set=valid_pool,
                                        verbose=False, use_best_model=True,
                                        )

    return cb_model


# %% ExecuteTime={"start_time": "2020-04-30T08:38:23.183315Z", "end_time": "2020-04-30T08:38:23.191364Z"}
def predictBaselineCatboost(trained_model, X_test, y_test, cat_features):

    test_pool  = Pool(X_test, y_test, cat_features=cat_features)
    score = trained_model.predict_proba(test_pool)[:,1]

    return score


# %% ExecuteTime={"start_time": "2020-04-30T08:38:23.193341Z", "end_time": "2020-04-30T08:38:23.198355Z"}
def evaluateBaselineCatboost(X_train, X_valid, X_test, y_train, y_valid, y_test,
                              cat_features=None,
                              **kwargs):
    """
    Обучение Catboost и оценка Gini на обучающей, валидационной и тестовой
    выборках
    """
    gini = []

    cb_model = trainBaselineCatbost(X_train, X_valid, y_train, y_valid,
                                    cat_features=cat_features,
                                    **kwargs)

    for x_i, y_i in [(X_train, y_train),
                     (X_valid, y_valid),
                     (X_test, y_test)]: # цикл по выборкам

        score = predictBaselineCatboost(cb_model, x_i, y_i, cat_features)
        AUC = roc_auc_score(y_i, score)
        gini.append(2 * AUC - 1)

    return gini


# %% ExecuteTime={"start_time": "2020-04-30T08:38:23.200360Z", "end_time": "2020-04-30T08:38:23.205373Z"}
def trainBaselineClassifier(classifier,
                            X_train,  y_train, **kwargs):
    clsf = classifier(**kwargs)
    clsf.fit(X_train, y_train)
    return clsf


# %% ExecuteTime={"start_time": "2020-04-30T08:38:23.207378Z", "end_time": "2020-04-30T08:38:23.213394Z"}
def evaluateBaselineClassifier(classifier,
                               X_train, X_val, X_test,
                               y_train, y_val, y_test,
                               **kwargs):
    """
    Обучение классификатора и расчет Gini
    """
    clsf = trainBaselineClassifier(classifier,
                                    X_train,  y_train,
                                   **kwargs)

    train_preds = clsf.predict_proba(X_train)[:,1]
    auc_train = roc_auc_score(y_train, train_preds)

    val_preds = clsf.predict_proba(X_val)[:,1]
    auc_val = roc_auc_score(y_val, val_preds)

    test_preds = clsf.predict_proba(X_test)[:,1]
    auc_test = roc_auc_score(y_test, test_preds)

    return list(map(lambda x: 2*x - 1, [auc_train, auc_val, auc_test]))


# %% ExecuteTime={"start_time": "2020-04-30T08:38:23.215399Z", "end_time": "2020-04-30T08:38:23.218408Z"} code_folding=[]
def predictBaselineClassifier(trained_classfifier, X_test, y_test):
    """
    Применить обученный классификатор к выборке
    """
    test_preds = trained_classfifier.predict_proba(X_test)[:,1]

    return test_preds

# %% ExecuteTime={"start_time": "2020-04-30T08:38:23.243006Z", "end_time": "2020-04-30T08:38:23.251027Z"} scrolled=false
def plot_roc(facts: list, preds: list, labels=['train', 'validate', 'test'], suptitle=None):
    roc_list = [] # [(FPR_train, TPR_train, thresholds_train), ...]
    gini_list = [] # [Gini_train, Gini_validate, Gini_test]

    lw=2 # толщина линий
 # подписи линий
    if len(preds) > 3:
        labels.extend([f'other_{i+1}' for i in range(len(preds)-3)])

    # Построение графика ROC
    plt.figure(figsize=(8, 8)) # размер рисунка
    for fact, p, label in zip(facts, preds, labels):
        fpr, tpr, _ = roc_curve(fact, p)
        gini = auc_to_gini(roc_auc_score(fact, p))
        roc_list.append((fpr, tpr))
        gini_list.append(gini)
        plt.plot(fpr, tpr, lw=lw,
                 label=f'{label} (Gini = {gini:.2%})', alpha=0.5)

    plt.plot([0, 1], [0, 1], color='k', lw=lw, linestyle='--', alpha=0.5)

    plt.xlim([-0.05, 1.05]) # min и max значения по осям
    plt.ylim([-0.05, 1.05])
    plt.xticks(fontsize=16)
    plt.yticks(fontsize=16)
    plt.grid(True)
    plt.xlabel('False Positive Rate', fontsize=14)
    plt.ylabel('True Positive Rate', fontsize=14)
    plt.title('ROC curves', fontsize=16)
    plt.legend(loc='lower right', fontsize=16)
    if suptitle is not None:
        plt.suptitle(suptitle, fontsize=20)
    plt.show()


def cramers_corr(x1, x2):
    """
    расчет V–коэффициент Крамера
    x1 - переменная 1
    x2 - переменная 2
    """
    confusion_matrix = pd.crosstab(x1, x2) # матрица запутанности
    chi2 = sts.chi2_contingency(confusion_matrix, correction=False)[0] # критерий Хи2 Пирсона
    n = confusion_matrix.sum().sum() # общая сумма частот в таблице сопряженности
    return np.sqrt(chi2 / (n * (min(confusion_matrix.shape) - 1)))

# %% ExecuteTime={"start_time": "2020-04-30T08:38:23.252031Z", "end_time": "2020-04-30T08:38:23.258048Z"}
def get_corr_matrices(data, method='pearson'):
    n = data.shape[1]
    cramers_mat = np.ones((n, n))
    print('Calculating Cramers correlations')
    row = 0
    pbar = tqdm(total=n)
    while row <= n:
        for i in range(n):
            if i > row:
                tmp_corr = cramers_corr(data.values[:, row], data.values[:, i])
                cramers_mat[row, i] = tmp_corr
                cramers_mat[i, row] = tmp_corr
        pbar.update(1)
        row += 1
    pbar.close()
    return data.corr(method=method), pd.DataFrame(cramers_mat,
                                                index=data.columns,
                                                columns=data.columns)

def select_feats_corr(data, corr_matrices=None,
                      sens_lin=0.7, sens_cramer=0.4, method='pearson'):
    if corr_matrices is None:
        corr_lin, corr_cramer = get_corr_matrices(data, method)
    else:
        corr_lin, corr_cramer = corr_matrices
    feat_list = [data.columns[0]]
    for x_i in data.columns:
        u = True
        for x_j in feat_list:
            if  (abs(corr_lin.loc[x_i, x_j]) > sens_lin
                 or corr_cramer.loc[x_i, x_j] > sens_cramer):
                    u = False
        if u:
            feat_list.append(x_i)

    return feat_list

def update_block_ref(ref_obj, block_name: str, features: list):
    block_list = pd.DataFrame.from_records([(block_name, i) for i in features])
    block_ref = pd.concat([ref_obj, block_list]).drop_duplicates()
    return block_ref



def baselines2df():
    with open('metrics/baselines.json', 'r', encoding='utf-8') as f:
        a = json.load(f)
    a = pd.DataFrame.from_dict(a, orient='index')
    b = pd.Series(a.index).str.split('_', expand=True).iloc[:, :-1]
    b.columns = ['model', 'bank', 'data_src', 'block' ]
    b['block'].fillna('Все фичи', inplace=True)
    return pd.concat([b, a.reset_index(drop=True)], axis=1, sort=False)






# %% ExecuteTime={"start_time": "2020-06-24T06:31:03.682421Z", "end_time": "2020-06-24T06:31:03.688436Z"} code_folding=[]
def plot_hier_corr(corr_matrix):
    '''
    Отрисовка дендрограммы иерархической кластеризации признаков
    по матрице корреляций

    TODO: добавить шкалу (или подписи) на тепловую карту
    '''
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 6))
    corr_linkage = hierarchy.ward(corr_matrix.values)
    dendro = hierarchy.dendrogram(corr_linkage, labels=corr_matrix.columns, ax=ax1,
                                   leaf_rotation=90)
    dendro_idx = np.arange(0, len(dendro['ivl']))
    ax1.hlines(ax1.get_yticks(), xmin=0, xmax=ax1.get_xlim()[1], linestyles='dotted', alpha=0.3)

    ax2.imshow(corr_matrix.values[dendro['leaves'], :][:, dendro['leaves']])
    ax2.set_xticks(dendro_idx)
    ax2.set_yticks(dendro_idx)
    ax2.set_xticklabels(dendro['ivl'], rotation='vertical')
    ax2.set_yticklabels(dendro['ivl'])
    fig.tight_layout()

    plt.show()


def select_features_hierarchy(df, thr, method='pearson'):
    """
    Отбор признаков по итогам иерархической кластеризации
    """
    corr_matrix = df.corr(method=method).values
    corr_linkage = hierarchy.ward(corr_matrix)
    cluster_ids = hierarchy.fcluster(corr_linkage, thr, criterion='distance')
    cluster_id_to_feature_ids = defaultdict(list)
    for idx, cluster_id in enumerate(cluster_ids):
        cluster_id_to_feature_ids[cluster_id].append(idx)
    selected_features = [v[0] for v in cluster_id_to_feature_ids.values()]


    return df.columns[selected_features]


def quick_test_logreg(X_train, X_test, y_train, y_test):
    lr = LogisticRegression(max_iter=1000)
    lr.fit(X_train, y_train)
    preds_train = lr.predict_proba(X_train)[:,1]
    preds_test = lr.predict_proba(X_test)[:,1]

    gini_train = auc_to_gini(roc_auc_score(y_train, preds_train))
    gini_test = auc_to_gini(roc_auc_score(y_train, preds_test))

    return {'gini_train':gini_train, 'gini_test':gini_test}




def test_hier_selection(X,
                        y,
                        best_feats,
                        seed=42,
                        verbose=False):
    """
    Построение моделей (логрег) до отбора и после отбора фичей и сравнение GIni
    """
    metric = {}
    # Тест исходных фичей
    X_train, X_test, y_train, y_test = train_test_split(X, y, stratify=y, random_state=seed)
    if verbose:
        print('Number of original feats:', len(X_train.columns))
    lr = LogisticRegression(max_iter=1000)
    lr.fit(X_train, y_train)
    auc = roc_auc_score(y_test, lr.predict_proba(X_test)[:,1])
    metric.update({'Original Gini': auc_to_gini(auc)})

    # Тест c фичами после отбора
    X_train, X_test, y_train, y_test = train_test_split(X[best_feats], y, stratify=y, random_state=seed)
    if verbose:
        print('Number of selected feats:', len(X_train.columns))
    lr = LogisticRegression(max_iter=1000)
    lr.fit(X_train, y_train)
    auc = roc_auc_score(y_test, lr.predict_proba(X_test)[:,1])
    metric.update({'Selected Gini': auc_to_gini(auc)})

    return metric


def plot_hier_selection(X, y, thr_list=None, seed=42):
    """
    Отрисовка Gini при разных уровнях порога отсечения фичей по корреляциям
    """
    if thr_list is None:
        thr_list = np.hstack((np.arange(0, 1, 0.1), np.arange(1, 6.1, 0.5)))
    selected_scores = []
    orig_scores = []
    for i in tqdm(thr_list, desc='Calculating scores over `thr_list`'):
        scores = test_hier_selection(X, y, select_features_hierarchy(X, i), seed=seed)
        selected_scores.append(scores.get('Selected Gini'))
        orig_scores.append(scores.get('Original Gini'))
    plt.plot(thr_list, selected_scores, label='Selected')
    plt.plot(thr_list, orig_scores, label='Original')
    plt.xlabel('Threshold')
    plt.ylabel('Gini')
    plt.title('Gini by selected threshold')
    plt.legend();




def dump_metrics(feat_list, y_train, y_test,
                 gini_train, gini_test,
                 hit_scores=None, min_hit_feat='', hit_train_size=0.):
    if hit_scores is None:
        hit_scores = {}
    gini_train_hit = hit_scores.get('train', 0.)
    gini_test_hit = hit_scores.get('test', 0.)

    summary = {
               'sample_size': len(y_train) + len(y_test),
               'test_size': np.round(len(y_test) / (len(y_train) + len(y_test)), 4),
               'train_target_rate': np.round(y_train.sum() / len(y_train), 7),
               'test_target_rate': np.round(y_test.sum() / len(y_test), 7),
               'gini_train': np.round(gini_train,7),
               'gini_test': np.round(gini_test, 7),
               'min_hit_feat':min_hit_feat,
               'hit_train_size':float(hit_train_size),
               'gini_train_hit': np.round(gini_train_hit,7),
               'gini_test_hit': np.round(gini_test_hit, 7),
               'selected_feats':", ".join(feat_list)
              }
    return summary


def build_logistic_regression(X_train, y_train, feat_list,
                               cv=5, use_woe=True,
                               param_grid=None,
                               woe_transformer=None,
                               random_seed=42):
    np.random.seed(random_seed)
    model_grid = LogisticRegression(penalty='l2', max_iter=1000, class_weight=None, random_state=random_seed)
    if use_woe:
      if isinstance(woe_transformer, WoeTransformer):
        wt = woe_transformer
      else:
        wt = WoeTransformer()
      pipe = Pipeline([('woe', wt),
                        ('logreg', model_grid)])
    else:
        pipe = model_grid

    if param_grid is None:
        param_grid = {'logreg__solver': ['lbfgs'],#['newton-cg', 'sag', 'saga', 'lbfgs'],
                      'logreg__C': [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0]}
    # подбор параметров
    grid_search = GridSearchCV(pipe, param_grid=param_grid, scoring='roc_auc', cv=cv)
    grid_search.fit(X_train[feat_list], y_train)

    return grid_search.best_estimator_


def get_gini_and_auc(facts:list, preds:list, plot=True, **kwargs):
    gini_list = []
    for f, p in zip(facts, preds):
        gini_list.append(auc_to_gini(roc_auc_score(f, p)))
    if plot:
        plot_roc(facts,
                           preds,
                           **kwargs)
    return gini_list


def calcGiniLR(X, y):
    scores = []
    for i in tqdm(X.columns, desc='1-factor Gini'):
        X1, X2, y1, y2 = train_test_split(X[[i]], y, stratify=y)
        lr = LogisticRegression(max_iter=500, random_state=42)
        lr.fit(X1, y1)

        preds1 = lr.predict_proba(X1)[:, 1]
        preds2 = lr.predict_proba(X2)[:, 1]

        score1 = auc_to_gini(roc_auc_score(y1, preds1))
        score2 = auc_to_gini(roc_auc_score(y2, preds2))

        scores.append((score1, score2))

    res = pd.DataFrame.from_records(scores, columns=['gini_train', 'gini_test'])
    res.insert(0, 'predictor', X.columns)
    return res


def select_features_corr(df, corr_matrices: tuple, pearson_sens=0.8, cramer_sens=0.8,
                      verbose=False):
    cols = df['predictor']
    if verbose: print('Got {} predictors'.format(len(cols)))
    pearson_df, cramer_df = corr_matrices

    X3 = []

    DF_predictors = pd.DataFrame({'predictor': cols}) # причина отсева предиктора
    L_reason = ['added']

    df_ = df.set_index('predictor').copy()

    for x_i in tqdm(cols): # цикл по отбираемым предикторам
        if len(X3) == 0:
            X3.append(x_i) # Добавляется предиктор с максимальным Gini train
            continue

        condition = True # проверка, что предиктор еще не отсеяли

        if df_.loc[x_i, 'gini_train'] < 0.05: # Gini
            condition = False
            if verbose: print(f'{x_i} - Gini')
            L_reason.append('Gini < 5%')

        if df_['IV'][x_i] < 0.05 and condition: # IV
            condition = False
            if verbose: print(f'{x_i} - IV')
            L_reason.append('IV < 5%')

        if condition:
            for x_j in X3: # цикл по отобранным предикторам
                if abs(pearson_df[x_i][x_j]) > pearson_sens and condition: # корреляция Пирсона
                    condition = False
                    if verbose: print(f'{x_i} - корреляция Пирсона с {x_j}')
                    L_reason.append(f'abs(Pearson) > {pearson_sens*100:.0f}% ({x_j})')
                if cramer_df[x_i][x_j] > cramer_sens and condition: # корреляция Крамера
                    condition = False
                    if verbose: print(f'{x_i} - корреляция Крамера с {x_j}')
                    L_reason.append(f'Cramer > {cramer_sens*100:.0f}% ({x_j})')

        if condition:
            X3.append(x_i)
            L_reason.append('added')

    DF_predictors['reason'] = L_reason
    if verbose: print('Selected {} predictors'.format(len(DF_predictors[DF_predictors['reason']=='added'])))

    return DF_predictors


def select_feats(X_train, y_train,
                 gini_and_iv_stats, pearson_corr, cramer_corr,
                 pearson_sens=0.8, cramer_sens=0.8,
                 random_seed=42):
    np.random.seed(random_seed)
    print('Got {} predictors'.format(len(X_train.columns)))
    gini_data = gini_and_iv_stats[['predictor', 'gini_train', 'gini_test']]
    iv_ordered_feats =  pd.Series(gini_and_iv_stats['IV'], index=gini_and_iv_stats['predictor'])
    gini_iv_subset = gini_and_iv_stats[gini_and_iv_stats['predictor'].isin(
        X_train.columns)]
    # Отбор фичей по корреляциям, Gini и IV
    corr_select_res = select_features_corr(gini_iv_subset,
                                           (pearson_corr, cramer_corr),
                                           pearson_sens=pearson_sens, cramer_sens=pearson_sens)
    # Исключение предикторов с положительными коэффициентами
    feat_list = corr_select_res.loc[corr_select_res['reason']
                                    == 'added', 'predictor'].to_list()
    feat_list = positive_coef_drop(
        X_train[feat_list], y_train, gini_data, iv_ordered_feats, seed=random_seed)
    print('Selected {} predictors'.format(len(feat_list)))

    return feat_list


def get_predictions(fitted_estimator, X):
    preds = fitted_estimator.predict_proba(X)[:, 1]

    return preds


def positive_coef_drop(X, y, gini_data, iv_ordered_feats, seed=42, verbose=False, enable_tqdm=False):
    """
    Удаление фичей с положительными коэффициентами
    """

    np.random.seed(seed)
    predictors = list(X.columns)
    if enable_tqdm:
        predictors = tqdm(predictors)
    for _ in predictors:  # исключение предикторов с положительными коэфициентами
        # подбор параметров
        skf = StratifiedKFold(n_splits=5, shuffle=False, random_state=seed)
        model_grid = LogisticRegression(
            penalty='l2', max_iter=500, random_state=seed)
        param_grid_model = {'solver': ['lbfgs'],  # ['newton-cg', 'sag', 'saga', 'lbfgs'],
                            'C': [0.01, 0.1, 0.5, 1.0, 2.0, 10.0]}
        grid_search = GridSearchCV(
            model_grid, param_grid_model, scoring='roc_auc', cv=skf)
        grid_search.fit(X[predictors], y)

        # анализ коэффициентов модели
        DF_model_inf = pd.DataFrame()
        DF_model_inf['predictor'] = predictors
        DF_model_inf['coef'] = grid_search.best_estimator_.coef_[0]
        # Используется внешний датафрейм с рассчитанными однофакторными Gini
        DF_model_inf = (DF_model_inf.merge(gini_data[['predictor', 'gini_train', 'gini_test']],
                                           how='left', on='predictor')
                        .rename(columns={'train': 'gini_tr', 'gini_t': 'Gini_test'}))
        # Используется внешний pd.Series с рассчитанными IV предикторов (и отсортированный по убыванию IV)
        DF_model_inf = DF_model_inf.merge(
            iv_ordered_feats, how='left', left_on='predictor', right_index=True)
        k_sum = (DF_model_inf['coef'] * DF_model_inf['IV']).sum()

        DF_model_inf['coef_K'] = DF_model_inf['coef'] * \
            DF_model_inf['IV'] / k_sum
        DF_model_inf_2 = DF_model_inf.loc[DF_model_inf['coef'] >= 0]   \
                                     .sort_values('IV').reset_index(drop=True)
        positive_coef_count = DF_model_inf_2.shape[0]
        if positive_coef_count > 0:
            x_i = DF_model_inf_2['predictor'][0]
            predictors.remove(x_i)
            if verbose:
                print(positive_coef_count, x_i)
                # display(DF_model_inf_2)
        else:
            break

    return predictors
