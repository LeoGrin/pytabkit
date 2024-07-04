from typing import Optional

from pytabkit.bench.data.common import TaskSource
from pytabkit.bench.data.get_uci import download_all_uci
from pytabkit.bench.data.import_tasks import import_uci_tasks, get_openml_task_ids, import_openml, get_openml_ds_names
from pytabkit.bench.data.paths import Paths
from pytabkit.bench.data.tasks import TaskCollection, TaskDescription, TaskInfo


def run_import(openml_cache_dir: str = None, import_meta_train: bool = True, import_meta_test: bool = True,
               import_openml_class_bin_extra: bool = False, import_grinsztajn: bool = False):
    paths = Paths.from_env_variables()
    min_n_samples = 1000

    if import_meta_train:
        # import UCI
        download_all_uci(paths)
        import_uci_tasks(paths)

        # generate task collections
        uci_multi_class_descs = TaskCollection.from_source(TaskSource.UCI_MULTI_CLASS, paths).task_descs
        uci_bin_class_descs = TaskCollection.from_source(TaskSource.UCI_BIN_CLASS, paths).task_descs
        uci_multi_class_task_names = [td.task_name for td in uci_multi_class_descs]
        uci_class_descs = uci_multi_class_descs + [td for td in uci_bin_class_descs
                                                   if td.task_name not in uci_multi_class_task_names]
        uci_class_descs = [td for td in uci_class_descs if td.load_info(paths).n_samples >= min_n_samples]
        TaskCollection('meta-train-class', uci_class_descs).save(paths)

        uci_reg_descs = TaskCollection.from_source(TaskSource.UCI_REGRESSION, paths).task_descs
        uci_reg_descs = [td for td in uci_reg_descs if td.load_info(paths).n_samples >= min_n_samples]
        TaskCollection('meta-train-reg', uci_reg_descs).save(paths)

    # maybe could use faster pyarrow backend for pandas if v2 is available?
    # pd.options.mode.dtype_backend = "pyarrow"

    if import_meta_test or import_openml_class_bin_extra:
        # import AutoML Benchmark and CTR-23 benchmark
        # could also import the TabZilla suite
        # https://www.openml.org/search?type=study&study_type=task&id=379&sort=tasks_included
        # but the selection criteria for this one are based a lot on the performance of different algorithms
        automl_class_task_ids = get_openml_task_ids(271)
        automl_reg_task_ids = get_openml_task_ids(269)
        ctr23_reg_task_ids = get_openml_task_ids(353)
        sarcos_duplicated_task_id = 361254
        sarcos_deduplicated_task_id = 361011
        if sarcos_duplicated_task_id in ctr23_reg_task_ids:
            # use the version of sarcos without the duplicated test set
            print(f'Using a different version of the sarcos data set for the CTR-23 benchmark')
            ctr23_reg_task_ids.remove(sarcos_duplicated_task_id)
            ctr23_reg_task_ids.append(sarcos_deduplicated_task_id)
        all_reg_task_ids = list(set(automl_reg_task_ids + ctr23_reg_task_ids))  # todo
        automl_class_ds_names = get_openml_ds_names(automl_class_task_ids)
        automl_reg_ds_names = get_openml_ds_names(automl_reg_task_ids)
        ctr23_reg_ds_names = get_openml_ds_names(ctr23_reg_task_ids)

        if import_meta_test:
            # treat dionis separately because we want to subsample it to 100k instead of 500k samples for speed and RAM reasons
            automl_class_task_ids_not_dionis = [id for id, name in zip(automl_class_task_ids, automl_class_ds_names)
                                                if name != 'dionis']
            automl_class_task_ids_dionis = [id for id, name in zip(automl_class_task_ids, automl_class_ds_names)
                                            if name == 'dionis']
            assert len(automl_class_task_ids_dionis) == 1
            assert len(automl_class_task_ids_not_dionis) == len(automl_class_task_ids) - 1

            import_openml(automl_class_task_ids_not_dionis, TaskSource.OPENML_CLASS, paths, openml_cache_dir,
                          max_n_samples=500_000, rerun=False)
            import_openml(automl_class_task_ids_dionis, TaskSource.OPENML_CLASS, paths, openml_cache_dir,
                          max_n_samples=100_000, rerun=True)

            import_openml(all_reg_task_ids, TaskSource.OPENML_REGRESSION, paths, openml_cache_dir, normalize_y=True,
                          max_n_samples=500000, rerun=False)

            class_descs = TaskCollection.from_source(TaskSource.OPENML_CLASS, paths).task_descs

            def check_task(td: TaskDescription, min_n_samples: Optional[int] = None,
                           max_one_hot_size: Optional[int] = None) -> bool:
                task_info = td.load_info(paths)
                if task_info.n_samples < min_n_samples:
                    print(f'Ignoring task {str(td)} because it has too few samples')
                    return False
                n_cont = task_info.tensor_infos['x_cont'].get_n_features()
                cat_sizes = task_info.tensor_infos['x_cat'].get_cat_sizes().numpy()
                # ignore 'missing' categories
                # todo: is this really the way we should handle this?
                d_one_hot = n_cont + sum([1 if cs == 3 else cs - 1 for cs in cat_sizes])
                if d_one_hot > max_one_hot_size:
                    print(f'Ignoring task {str(td)} because it is too high-dimensional after one-hot encoding')
                    return False
                return True

            # generate task collections
            exclude_automl_class = ['kr-vs-kp', 'wilt', 'ozone-level-8hr', 'first-order-theorem-proving',
                                    'GesturePhaseSegmentationProcessed', 'PhishingWebsites', 'wine-quality-white',
                                    'nomao',
                                    'bank-marketing', 'adult']
            filtered_class_descs = [td for td in class_descs if td.task_name not in exclude_automl_class
                                    and td.task_name in automl_class_ds_names
                                    and check_task(td, min_n_samples=min_n_samples, max_one_hot_size=10000)]
            TaskCollection('meta-test-class', filtered_class_descs).save(paths)

            # we exclude Brazilian_houses because there is already brazilian_houses in ctr-23,
            # and Brazilian_houses includes three features that should not be used for predicting the target,
            # while brazilian_houses should not contain them
            exclude_automl_reg = ['wine_quality', 'abalone', 'OnlineNewsPopularity', 'Brazilian_houses']
            exclude_ctr23_reg = ['abalone', 'physiochemical_protein', 'naval_propulsion_plant', 'superconductivity',
                                 'white_wine', 'red_wine', 'grid_stability']
            # todo: check if dataset names match
            reg_descs = TaskCollection.from_source(TaskSource.OPENML_REGRESSION, paths).task_descs
            filtered_reg_descs = [td for td in reg_descs if td.task_name not in exclude_automl_reg + exclude_ctr23_reg
                                  and td.task_name in automl_reg_ds_names + ctr23_reg_ds_names
                                  and check_task(td, min_n_samples=min_n_samples, max_one_hot_size=10000)]
            TaskCollection('meta-test-reg', filtered_reg_descs).save(paths)

        if import_openml_class_bin_extra:
            # also import binary version of multiclass tasks
            # requires that meta_test has already been imported
            class_descs = TaskCollection.from_source(TaskSource.OPENML_CLASS, paths).task_descs
            multiclass_names = [td.task_name for td in class_descs if td.load_info(paths).get_n_classes() > 2]
            # print(f'{multiclass_names=}')
            import_openml(automl_class_task_ids, TaskSource.OPENML_CLASS_BIN_EXTRA, paths, openml_cache_dir,
                          max_n_classes=2, include_only_ds_names=multiclass_names)

    if import_grinsztajn:
        import_grinsztajn_datasets(openml_cache_dir)


def import_grinsztajn_datasets(openml_cache_dir: str = None):
    # import data sets from the benchmark of Grinsztajn et al.
    paths = Paths.from_env_variables()
    import_openml(get_openml_task_ids(334), 'grinsztajn-cat-class', paths, openml_cache_dir,
                  max_n_samples=500000,
                  rerun=False)
    import_openml(get_openml_task_ids(335), 'grinsztajn-cat-reg', paths, openml_cache_dir,
                  normalize_y=True, max_n_samples=500000,
                  rerun=False)
    import_openml(get_openml_task_ids(336), 'grinsztajn-num-reg', paths, openml_cache_dir,
                  normalize_y=True, max_n_samples=500000,
                  rerun=False)
    import_openml(get_openml_task_ids(337), 'grinsztajn-num-class', paths, openml_cache_dir,
                  max_n_samples=500000,
                  rerun=False)

    import_openml(get_openml_task_ids(334), 'grinsztajn-cat-class-15k', paths, openml_cache_dir,
                  max_n_samples=15_000,
                  rerun=False)
    import_openml(get_openml_task_ids(335), 'grinsztajn-cat-reg-15k', paths, openml_cache_dir,
                  normalize_y=True, max_n_samples=15_000,
                  rerun=False)
    import_openml(get_openml_task_ids(336), 'grinsztajn-num-reg-15k', paths, openml_cache_dir,
                  normalize_y=True, max_n_samples=15_000,
                  rerun=False)
    import_openml(get_openml_task_ids(337), 'grinsztajn-num-class-15k', paths, openml_cache_dir,
                  max_n_samples=15_000,
                  rerun=False)


def split_meta_test(paths: Paths):
    for task_type in ['class', 'reg']:
        coll_name = f'meta-test-{task_type}'
        task_infos = TaskCollection.from_name(coll_name, paths).load_infos(paths)

        def is_ood(task_info: TaskInfo):
            if task_info.n_samples < 1500 or task_info.n_samples > 60000:
                return True
            n_features = (task_info.tensor_infos['x_cont'].get_n_features()
                          + task_info.tensor_infos['x_cat'].get_n_features())
            if n_features > 750:
                return True
            x_cat_info = task_info.tensor_infos['x_cat']
            if x_cat_info.get_n_features() > 0 and x_cat_info.get_cat_sizes().max().item() > 50:
                return True
            return False

        id_task_descs = [task_info.task_desc for task_info in task_infos if not is_ood(task_info)]
        ood_task_descs = [task_info.task_desc for task_info in task_infos if is_ood(task_info)]

        TaskCollection(f'{coll_name}-indist', id_task_descs).save(paths)
        TaskCollection(f'{coll_name}-oodist', ood_task_descs).save(paths)

        print(f'{len(id_task_descs)=}, {len(ood_task_descs)=}')


# could extend this for other task collections like openml-cc18, pmlb, uci121 or uci-small

if __name__ == '__main__':
    # fire.Fire(run_import)
    # import_grinsztajn_datasets()
    paths = Paths.from_env_variables()
    # split_meta_test(paths)

    meta_train = TaskCollection.from_name('meta-train-class', paths).load_infos(paths)
    only_bin_class = [info.task_desc for info in meta_train if info.get_n_classes() == 2]
    only_multi_class = [info.task_desc for info in meta_train if info.get_n_classes() > 2]
    TaskCollection('meta-train-bin-class', only_bin_class).save(paths)
    TaskCollection('meta-train-multi-class', only_multi_class).save(paths)

    # print(get_openml_ds_names([361011]))
    # ctr23_reg_task_ids = get_openml_task_ids(353)
    # ctr23_reg_ds_names = get_openml_ds_names(ctr23_reg_task_ids)
    # for ds_name in ctr23_reg_ds_names:
    #     print(ds_name)

    # test brazilian houses data set
    # import openml
    # import pandas as pd
    # task = openml.tasks.get_task(361267, download_data=False)
    # dataset = openml.datasets.get_dataset(task.dataset_id, download_data=True)
    # df: pd.DataFrame = dataset.get_data()[0]
    # print(df.head())
    # print(dataset.dataset_id)

    # test sarcos dataset
    # import openml
    # task = openml.tasks.get_task(361011, download_data=False)
    # dataset = openml.datasets.get_dataset(task.dataset_id, download_data=False)
    # print(dataset.dataset_id)
