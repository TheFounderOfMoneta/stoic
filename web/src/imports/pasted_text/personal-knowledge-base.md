Личная База Знаний — Полное описание проекта
Сейчас разберу всё по порядку: идею, архитектуру, таблицы и примеры.

Что это такое
Это личная база знаний, которая живёт всю жизнь. Она сама собирает информацию из всех твоих источников — почты, Telegram, файлов, заметок, календаря — и превращает её в структурированные карточки с историей, связями и доказательствами. Ты только подтверждаешь важное, а не вводишь всё руками.
Главная идея: хранить не просто данные, а знание о том, как данные менялись со временем.

Принципы системы
Сначала сырьё, потом знание. Всё, что приходит извне, сначала сохраняется как есть. Потом система предлагает смысл. Потом ты подтверждаешь.
LLM не пишет в истину напрямую. Она только предлагает кандидатов. Человек решает.
Каждый факт имеет доказательство. Нельзя создать факт без ссылки на фрагмент источника.
История не удаляется. Старые факты не затираются, а помечаются как заменённые.

Архитектура: слои системы
Источники (сырьё)
    ↓
Фрагменты (точные куски)
    ↓
Кандидаты (предложения LLM)
    ↓
Очередь проверки (твоё решение)
    ↓
Сущности / Факты / Связи (рабочая правда)
    ↓
Поисковый слой (search_documents + векторный индекс)
    ↓
LLM собирает ответ из готового пакета

Все таблицы
Блок 1 — Словари

entity_types — «Типы сущностей»
Говорит системе, какие вообще бывают виды карточек.
ПолеТипЗачемidtext PKКороткий код типа. Например: person, task, project. По нему ссылаются все другие таблицы.nametextЧеловекочитаемое название для интерфейса. Например: «Человек», «Задача».descriptiontextОбъяснение, чем этот тип отличается от похожих. Например, чем idea отличается от project.statustextИспользуется тип сейчас или архивирован. Значения: active, archived. Нужно, чтобы старые типы не ломали историю.
Пример данных:
idnamedescriptionstatuspersonЧеловекЖивой человекactivecompanyКомпанияОрганизация или юрлицоactivetaskЗадачаЧто нужно сделатьactiveideaИдеяМысль или задумкаactiveprojectПроектБольшая работа с цельюactivetopicТемаУстойчивая тема интересаactivedocumentДокументФайл, статья, PDFactiveeventСобытиеВстреча, митап, звонок, праздникactiveplaceМестоГород, офис, кафеactivegameИграКонкретная игра или игровой проектactive

fact_types — «Типы фактов»
Словарь того, что вообще можно знать о карточке.
ПолеТипЗачемidtext PKКод типа. Например: role, task_status, birthday.nametextНазвание для интерфейса.value_kindtextКакого типа значение: text, date, number, boolean, json. Нужно, чтобы база знала, как работать с содержимым.series_modetextКак этот факт живёт во времени: single_current — в один момент только одна версия, multi_active — может быть несколько одновременно.descriptiontextКраткое объяснение, чтобы не путать близкие типы.statustextАктивен или архивирован.
Пример данных:
idnamevalue_kindseries_modedescriptionroleРольtextsingle_currentТекущая должность человекаtask_statusСтатус задачиtextsingle_currentТекущий статус: new, in_progress, donedeadlineКрайний срокdatesingle_currentДедлайн задачи или событияstart_dateДата началаdatesingle_currentКогда начать работу над задачейevent_dateДата событияdatesingle_currentКогда произойдёт событиеlanguageЯзыкtextmulti_activeЯзык общенияbirthdayДень рожденияdatesingle_currentДата рожденияimportance_noteЛичная пометкаtextmulti_activeПочему это важно личноpriorityПриоритетtextsingle_currentПриоритет задачи: low, medium, highlocationМестоtextsingle_currentГде происходит событие или задачаreminder_atНапоминаниеdatesingle_currentКогда напомнитьdescriptionОписаниеtextsingle_currentСвободное описание

relation_types — «Типы связей»
Словарь допустимых видов ниточек между карточками.
ПолеТипЗачемidtext PKКод типа связи.nametextНазвание для интерфейса.series_modetextsingle_current или multi_active. Место работы — single. Интересы — multi.descriptiontextОбъяснение смысла связи.directedbooleanЕсть ли направление у связи. «Алексей работает в компании» — направленная.statustextАктивен или архивирован.
Пример данных:
idnameseries_modedirecteddescriptionworks_atРаботает вsingle_currenttrueЧеловек работает в компанииinterested_inИнтересуетсяmulti_activetrueЧеловек интересуется темойgave_taskДал задачуmulti_activetrueЧеловек поставил задачуgrew_fromВыросло изmulti_activetrueПроект вырос из идеиrelated_toСвязано сmulti_activefalseОбщая смысловая связьparticipated_inУчаствовал вmulti_activetrueЧеловек участвовал в событииinvited_toПригласил наmulti_activetrueЧеловек пригласил на событиеassigned_toНазначена наsingle_currenttrueЗадача назначена человекуaboutО чёмmulti_activetrueЗадача / разговор о теме или сущности

Блок 2 — Главные карточки мира

entities — «Сущности»
Главный склад всех карточек. Одна строка = один объект реального мира.
ПолеТипЗачемidtext PKПостоянный идентификатор. Никогда не меняется, даже если имя поменялось. Формат: E000001.type_idtext FK → entity_typesВид сущности. По нему интерфейс знает, как отображать карточку.statustextСостояние карточки: active, merged, hidden, archived.descriptiontextКороткое описание для быстрого понимания ещё до открытия.created_attimestamptzКогда карточка появилась в базе.updated_attimestamptzКогда последний раз менялась. Нужно для понимания свежести.merged_into_entity_idtext FK → entitiesЕсли объединили с другой карточкой — ссылка на новую главную. Старая не удаляется.
Пример данных:
idtype_idstatusdescriptioncreated_atE000001personactiveЭто ты2026-01-01E000002personactiveДима однокурс, Telegram: ThebestRofer2026-04-22E000003eventactiveДнюха Димы 23 апреля 20262026-04-22E000004taskactiveСозвониться с Димой по игре2026-04-22E000005personactiveАлексей Петров2026-04-11E000006companyactiveБетаЛаб2026-04-11E000007companyactiveГаммаАИ2027-01-15E000008topicactiveGraphRAG2026-04-11E000009ideaactiveЛичная база знаний на всю жизнь2026-04-02E000010projectactiveAI & Personal Knowledge Base2026-04-15E000011documentactiveОбзор GraphRAG 20252026-04-12E000012eventactiveMoscow AI Meetup 2026-04-112026-04-11

entity_names — «Имена сущностей»
Одна сущность может называться по-разному в разных источниках.
ПолеТипЗачемidtext PKНомер записи имени.entity_idtext FK → entitiesК какой карточке относится имя.nametextСам текст имени.kindtextТип: primary — главное, alias — псевдоним, variant — альтернативное написание.languagetextЯзык: ru, en. Нужно для поиска по разным написаниям.source_idtext FK → sourcesОткуда взялось это имя. Нужно для проверки.created_attimestamptzКогда добавили. Нужно для разборов дублей.
Пример данных:
identity_idnamekindlanguagesource_idEN000001E000002Дима однокурсprimaryruS000001EN000002E000002ThebestRoferaliasenS000001EN000003E000005Алексей ПетровprimaryruS000003EN000004E000005Alexey PetrovaliasenS000008EN000005E000006БетаЛабprimaryruS000004EN000006E000006BetaLabvariantenS000004EN000007E000010AI & Personal Knowledge BaseprimaryenS000006EN000008E000010Личная база знаний с ИИaliasruS000006

fact_series — «Серии фактов»
Папки, которые объединяют версии одного свойства. Нужны для быстрого ответа: «что сейчас актуально?»
ПолеТипЗачемidtext PKНомер серии.entity_idtext FK → entitiesУ какой сущности эта серия.fact_type_idtext FK → fact_typesКакое свойство хранится в этой серии.modetextsingle_current или multi_active.current_fact_idtext FK → factsУказатель на текущий активный факт. Только для single_current. Позволяет не перебирать всю историю.statustextЖива серия или архивирована.created_attimestamptzКогда серия появилась.updated_attimestamptzКогда менялась.
Пример данных:
identity_idfact_type_idmodecurrent_fact_idstatusFS000001E000005rolesingle_currentF000002activeFS000002E000004task_statussingle_currentF000006activeFS000003E000004deadlinesingle_currentF000007activeFS000004E000003event_datesingle_currentF000008activeFS000005E000005importance_notemulti_activeactive

facts — «Факты»
Конкретные версии свойств. Это главная таблица знания о сущностях.
ПолеТипЗачемidtext PKНомер версии факта.series_idtext FK → fact_seriesВ какой папке лежит эта версия.entity_idtext FK → entitiesУ какой сущности этот факт. Дублирует связь через серию — для быстрых выборок.fact_type_idtext FK → fact_typesКакого типа этот факт.value_jsonjsonbСамо значение. Главное содержимое факта.statustextactive, superseded (заменён), corrected (исправлен), retracted (отозван).confidencefloatУверенность в факте от 0 до 1. Для ранжирования и review.valid_fromtimestamptzС какого момента это было правдой в реальной жизни.valid_totimestamptzДо какого момента. Заполняется при появлении нового факта в серии.recorded_fromtimestamptzКогда база впервые записала этот факт. Может отличаться от valid_from.recorded_totimestamptzКогда база перестала считать эту версию текущей.last_verified_attimestamptzКогда факт проверяли последний раз. Для поиска устаревших данных.replaces_fact_idtext FK → factsКакой предыдущий факт эта версия заменяет. Только прямой сосед, не вся цепочка.replaced_by_fact_idtext FK → factsКаким следующим фактом эта версия заменена. Тоже только прямой сосед.primary_source_fragment_idtext FK → source_fragmentsГлавный фрагмент-доказательство. Без него факт не может быть подтверждён.created_bytextuser, system, llm. Происхождение записи.
Пример данных:
idseries_identity_idfact_type_idvalue_jsonstatusvalid_fromvalid_toreplaces_fact_idreplaced_by_fact_idF000001FS000001E000005role{"text":"Product Lead"}superseded2026-04-112027-01-14F000002F000002FS000001E000005role{"text":"Director of Product"}active2027-01-15F000001F000003FS000002E000004task_status{"text":"new"}superseded2026-04-222026-04-23F000004F000004FS000002E000004task_status{"text":"in_progress"}superseded2026-04-232026-04-25F000003F000005F000005FS000002E000004task_status{"text":"done"}active2026-04-25F000004F000006FS000002E000004task_status{"text":"new"}superseded2026-04-22F000003F000007FS000003E000004deadline{"date":"2026-04-25"}active2026-04-22F000008FS000004E000003event_date{"date":"2026-04-23"}active2026-04-23

relation_series — «Серии связей»
То же, что fact_series, но для ниточек между двумя сущностями.
ПолеТипЗачемidtext PKНомер серии.from_entity_idtext FK → entitiesНачало связи.relation_type_idtext FK → relation_typesВид связи.to_entity_idtext FK → entitiesКонец связи.modetextsingle_current или multi_active.current_relation_idtext FK → relationsТекущая активная версия для single_current.statustextЖива серия или нет.created_attimestamptzКогда появилась.updated_attimestamptzКогда менялась.
Пример данных:
idfrom_entity_idrelation_type_idto_entity_idmodecurrent_relation_idRS000001E000005works_atE000006single_currentR000002RS000002E000005interested_inE000008multi_activeRS000003E000010grew_fromE000009multi_activeRS000004E000002invited_toE000003multi_activeRS000005E000004aboutE000008multi_active

relations — «Связи»
Конкретные ниточки между сущностями с историей.
ПолеТипЗачемidtext PKНомер версии связи.series_idtext FK → relation_seriesВ какой папке лежит версия.from_entity_idtext FK → entitiesНачало связи.relation_type_idtext FK → relation_typesТип связи.to_entity_idtext FK → entitiesКонец связи.statustextactive, superseded, corrected, retracted.confidencefloatУверенность от 0 до 1.valid_fromtimestamptzС какого момента связь была правдой.valid_totimestamptzДо какого момента.recorded_fromtimestamptzКогда база записала эту версию.recorded_totimestamptzКогда перестала считать текущей.last_verified_attimestamptzКогда последний раз проверяли.replaces_relation_idtext FK → relationsКакую версию заменила.replaced_by_relation_idtext FK → relationsКакая версия заменила её.primary_source_fragment_idtext FK → source_fragmentsДоказательство.created_bytextuser, system, llm.
Пример данных:
idseries_idfrom_entity_idrelation_type_idto_entity_idstatusvalid_fromvalid_toreplaces_relation_idreplaced_by_relation_idR000001RS000001E000005works_atE000006superseded2026-04-112027-01-14R000002R000002RS000001E000005works_atE000007active2027-01-15R000001R000003RS000002E000005interested_inE000008active2026-04-11R000004RS000003E000010grew_fromE000009active2026-04-15R000005RS000004E000002invited_toE000003active2026-04-22

Блок 3 — Сырьё и доказательства

source_accounts — «Учётки источников»
Подключённые интеграции. Ещё не данные, а «краны», из которых льётся поток.
ПолеТипЗачемidtext PKНомер подключения.module_typetextТип: gmail, telegram, google_calendar, file_ingest, voice.account_labeltextУдобное имя: «Личная почта», «AI-каналы».statustextactive, paused, revoked.settings_jsonjsonbНастройки: интервал синхронизации, ID каналов, фильтры.created_attimestamptzКогда подключили.updated_attimestamptzКогда настройки менялись.

sources — «Источники»
Сырой вход. Всё, что реально пришло из внешнего мира.
ПолеТипЗачемidtext PKНомер.source_typetextТип: note, email, chat_message, voice_note, file, calendar_event.origintextОткуда: manual_input, gmail, telegram, google_calendar, web_download.source_account_idtext FK → source_accountsЧерез какую учётку пришёл. Для интеграций.url_or_pathtextГде лежит оригинал. Нужно уметь открыть источник.captured_attimestamptzКогда захвачен системой.statustextcaptured, normalized, processed, error.raw_texttextСырой текст как пришёл. Не менять никогда.raw_blob_reftextПуть к бинарному файлу если это не текст.metadata_jsonjsonbМетаданные: отправитель, тема письма, username, длительность аудио.

source_fragments — «Фрагменты источников»
Точные куски источников. Именно на них ссылаются факты и связи как на доказательства.
ПолеТипЗачемidtext PKНомер фрагмента.source_idtext FK → sourcesИз какого источника вырезан.locatortextГде именно внутри: paragraph_1, sentence_3, signature, subject.texttextОригинальный текст. Доказательство.normalized_texttextОчищенная версия для поиска и сравнения.fragment_typetextВид: sentence, paragraph, signature, header, block.embedding_reftextСсылка на векторный индекс для семантического поиска.created_attimestamptzКогда создан.

attachments — «Вложения»
Файлы с ручным контекстом. Самое важное здесь — поля manual_*, которые хранят твой личный смысл.
ПолеТипЗачемidtext PKНомер.source_idtext FK → sourcesК какому источнику привязан файл.pathtextГде лежит на диске.mime_typetextТип файла: application/pdf, audio/m4a, image/jpeg.size_bytesbigintРазмер для контроля хранения.checksum_sha256textКонтрольная сумма. Нужна для проверки целостности и поиска дублей.extracted_texttextТекст, вытащенный автоматически из файла.auto_summarytextАвтоматическое краткое описание.manual_titletextРучное название. Оригинальное имя файла часто бесполезно.manual_why_savedtextПочему сохранил. Это невозможно угадать автоматически.manual_how_to_findtextКак потом искать. Твои собственные ключевые слова.manual_main_pointtextЧто главное в файле лично для тебя.manual_keywordstextТвои теги для поиска.manual_review_afterdateКогда стоит вернуться к файлу.created_attimestamptzКогда добавлен.

Блок 4 — Служебные таблицы

source_import_jobs — «Задания импорта»
Журнал работы модулей добавления данных.
ПолеТипЗачемidtext PKНомер задания.module_typetextКакой модуль работал.source_account_idtext FKЧерез какую учётку.prioritytextnormal, heavy, urgent. Для очередности обработки.statustextqueued, running, success, error.started_attimestamptzКогда стартовало.finished_attimestamptzКогда закончилось.items_foundintСколько объектов нашёл модуль.items_savedintСколько реально сохранено.error_texttextТекст ошибки если была. Для отладки.

candidate_facts — «Кандидаты в факты»
Черновики фактов, предложенные системой. Не истина, а предложение.
ПолеТипЗачемidtext PKНомер.entity_idtext FK → entitiesК какой сущности, если она уже существует.proposed_entity_type_idtext FK → entity_typesЕсли сущности ещё нет — какой тип создать.fact_type_idtext FK → fact_typesТип предлагаемого факта.value_jsonjsonbПредлагаемое значение.old_value_jsonjsonbСтарое значение для сравнения «было / стало».proposed_series_idtext FK → fact_seriesВ какую серию попадёт после подтверждения.statustextnew, needs_review, approved, rejected, applied.risk_leveltextlow, medium, high. Высокий риск не применяется автоматически.confidencefloatУверенность системы.valid_fromtimestamptzЕсли система поняла дату начала правды.valid_totimestamptzЕсли поняла дату конца.source_fragment_idtext FK → source_fragmentsОткуда вывод.model_nametextКакая модель предложила. Для аудита.prompt_versiontextВерсия промпта. Для воспроизводимости.created_attimestamptzКогда создан.

candidate_relations — «Кандидаты в связи»
Черновики связей. Та же логика что у candidate_facts.
ПолеТипЗачемidtext PKНомер.from_entity_idtext FK → entitiesНачало предлагаемой связи.relation_type_idtext FK → relation_typesТип связи.to_entity_idtext FK → entitiesКонец связи.proposed_series_idtext FK → relation_seriesВ какую серию попадёт после подтверждения.statustextnew, needs_review, approved, rejected, applied.risk_leveltextlow, medium, high.confidencefloatУверенность.valid_fromtimestamptzДата начала если известна.valid_totimestamptzДата конца если известна.source_fragment_idtext FK → source_fragmentsДоказательство.model_nametextКакая модель.prompt_versiontextВерсия промпта.created_attimestamptzКогда создан.

review_queue — «Очередь проверки»
Твой защитный экран. Всё высокорисковое проходит через тебя.
ПолеТипЗачемidtext PKНомер.item_kindtextЧто лежит в очереди: candidate_fact, candidate_relation, attachment_context, merge_candidate.item_idtextНомер конкретного объекта.risk_leveltextlow, medium, high.statustextqueued, in_review, approved, rejected, skipped.reasontextПочему попал в очередь. Нужно чтобы понять, что именно решать.priority_scorefloatЧисловой приоритет для сортировки очереди.created_attimestamptzКогда попал в очередь.resolved_attimestamptzКогда решили.resolved_bytextКто решил: user, auto.

history — «История»
Журнал всего, что менялось. Нужна не для красоты — нужна чтобы откатывать ошибки и понимать когда всё изменилось.
ПолеТипЗачемidtext PKНомер события.object_kindtextЧто изменилось: entity, fact, relation, source, attachment, review_queue.object_idtextКакой именно объект.actiontextЧто произошло: create, update, approve, reject, supersede, merge, archive.old_value_jsonjsonbЧто было до.new_value_jsonjsonbЧто стало после.actor_kindtextКто действовал: user, system, llm, module.actor_idtextУточнение кто именно. Для аудита.source_reftextС каким источником или заданием связано изменение.created_attimestamptzКогда произошло.

Блок 5 — Поисковый слой

search_documents — «Поисковые документы»
Собранные человекочитаемые тексты по сущностям. Нужны для семантического поиска. Не хранят истину — хранят текст для индексирования.
ПолеТипЗачемidtext PKНомер.entity_idtext FK → entitiesК какой сущности относится документ.contenttextСобранный текст из имён, фактов, связей и источников.embeddingvector(1536)Векторное представление. Нужно для pgvector.built_attimestamptzКогда пересобран. Нужно для инвалидации.
Пример content для E000002 (Дима):

Дима однокурс, Telegram: ThebestRofer. Знакомый с универа. Пригласил на днюху 23 апреля 2026. Просил созвониться по игре. Источник: Telegram-сообщение 22 апреля 2026.


Полный живой сценарий
Покажу один эпизод от А до Я: пришло Telegram-сообщение и прошло через все таблицы.
Исходное сообщение:
username: ThebestRofer, nickname: Дима однокурс
text: Здарова бро приглашаю тебя на днюху свою 23 апреля. Также у меня к тебе есть вопросики по нашей игре давай созвонимся

Шаг 1. Модуль Telegram принимает сообщение
source_import_jobs:
idmodule_typesource_account_idstatusitems_founditems_savedJ000001telegramSA000001success11
sources:
idsource_typeorigincaptured_atstatusraw_textmetadata_jsonS000001chat_messagetelegram2026-04-22 19:00capturedЗдарова бро приглашаю...{"username":"ThebestRofer","nickname":"Дима однокурс"}

Шаг 2. Текст режется на фрагменты
source_fragments:
idsource_idlocatortextfragment_typeSF000001S000001sentence_1Здарова бро приглашаю тебя на днюху свою 23 апреляsentenceSF000002S000001sentence_2Также у меня к тебе есть вопросики по нашей игре давай созвонимсяsentenceSF000003S000001metadatausername: ThebestRofer, nickname: Дима однокурсmetadata

Шаг 3. LLM предлагает кандидатов
Важно: «наша игра» не становится сущностью автоматически. Это неразрешённый контекст — пока просто текст в кандидате задачи.
candidate_facts:
identity_idfact_type_idvalue_jsonstatusrisk_levelconfidencesource_fragment_idCF000001(новый)—{"name":"Дима однокурс","alias":"ThebestRofer","type":"person"}needs_reviewhigh0.97SF000003CF000002(новый)event_date{"date":"2026-04-23","context":"днюха Димы"}needs_reviewmedium0.98SF000001CF000003(новый)task_status{"text":"new","context":"созвониться с Димой по игре"}needs_reviewmedium0.93SF000002
candidate_relations:
idfrom_entity_idrelation_type_idto_entity_idstatusrisk_levelconfidencesource_fragment_idCR000001(Дима)invited_to(событие днюха)needs_reviewlow0.97SF000001CR000002(задача созвон)about(неразрешённый контекст: «наша игра»)needs_reviewmedium0.85SF000002

Шаг 4. Всё летит в очередь проверки
review_queue:
iditem_kinditem_idrisk_levelstatusreasonRQ000001candidate_factCF000001highqueuedНовый человек. Нужно подтвердить создание карточки.RQ000002candidate_factCF000002mediumqueuedДата события понята из текста. Это дата праздника, не рождения.RQ000003candidate_factCF000003mediumqueuedЗадача на созвон — автосоздание, нужно уточнить детали.RQ000004candidate_relationCR000001lowqueuedПриглашение на событие. Очевидно.RQ000005candidate_relationCR000002mediumqueued«Наша игра» не разрешена. Нужно уточнить, что за игра.

Шаг 5. Ты открываешь экран Review
Для CF000001 (новый человек) — форма уточнения для person:
Имя: Дима однокурс
Псевдоним: ThebestRofer
Платформа: Telegram
Откуда знаешь: [однокурсник / коллега / случайный / другое]
Добавить заметку: _______
Для CF000003 (задача) — форма уточнения для task:
Название: Созвониться с Димой
О чём: [выбрать из сущностей / ввести]
Срок: [до 23 апреля / позже]
Напомнить: [22 апреля / сам]
Приоритет: [высокий / средний / низкий]
Для CR000002 (наша игра) — форма уточнения:
«наша игра» — что это?
[ ] Существующий проект (выбрать из списка)
[ ] Создать новую карточку: Game / Project
[ ] Пропустить, уточнить позже

Шаг 6. После подтверждения — всё становится рабочим знанием
entities:
idtype_idstatusdescriptionE000002personactiveДима однокурс, Telegram: ThebestRoferE000003eventactiveДнюха Димы 23 апреля 2026E000004taskactiveСозвониться с Димой
fact_series + facts для задачи:
FS identity_idfact_type_idmodecurrent_fact_idFS000002E000004task_statussingle_currentF000003
F idseries_idvalue_jsonstatusvalid_fromF000003FS000002{"text":"new"}active2026-04-22

Шаг 7. История фиксирует всё
history:
idobject_kindobject_idactionactor_kindcreated_atH000001sourceS000001createmodule2026-04-22 19:00H000002entityE000002createsystem2026-04-22 19:01H000003entityE000002approveuser2026-04-22 19:05H000004entityE000003createsystem2026-04-22 19:01H000005factF000003approveuser2026-04-22 19:05H000006relationR000005approveuser2026-04-22 19:05

Что видишь в интерфейсе потом
Карточка Димы:
Дима однокурс
alias: ThebestRofer (Telegram)
Однокурсник

События:
  22 апреля 2026 — написал в Telegram
  Пригласил на днюху 23 апреля

Связанное:
  Задача: Созвониться по игре [новая]
  Событие: Днюха 23 апреля 2026

Источники:
  Telegram-сообщение 22 апреля 2026
Карточка задачи:
Созвониться с Димой
Статус: новая
Срок: до 23 апреля
О чём: [наша игра — не разрешено]
Источник: Telegram, 22 апреля

Главное что нельзя забыть
Карточка — это не всё. Она хранит только основу. Смысл живёт в фактах, связях и источниках.
Факт — это отдельная строка с историей, не просто поле в карточке.
Связь — тоже отдельная строка с историей.
Источник и фрагмент — обязательны. Без них нельзя доказать откуда что взялось.
Кандидат — это не истина. Это предложение на согласование.
История — не для красоты. Она позволяет откатывать ошибки и понимать когда что изменилось.
fact_series — не для смысла, а для навигации. Она говорит системе какой факт сейчас текущий, чтобы не перебирать всю историю.
LLM не пишет в рабочие таблицы напрямую — только через кандидатов и твоё подтверждение.