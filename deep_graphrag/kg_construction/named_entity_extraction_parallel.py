# mypy: ignore-errors
import argparse
from functools import partial
from multiprocessing import Pool

import numpy as np
import pandas as pd
from langchain_community.chat_models import ChatLlamaCpp, ChatOllama
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from tqdm import tqdm

from .langchain_util import init_langchain_model
from .processing import extract_json_dict

query_prompt_one_shot_input = """Please extract all named entities that are important for solving the questions below.
Place the named entities in json format.

Question: Which magazine was started first Arthur's Magazine or First for Women?

"""
query_prompt_one_shot_output = """
{"named_entities": ["First for Women", "Arthur's Magazine"]}
"""

query_prompt_template = """
Question: {}

"""


def named_entity_recognition(client, text: str):
    query_ner_prompts = ChatPromptTemplate.from_messages(
        [
            SystemMessage("You're a very effective entity extraction system."),
            HumanMessage(query_prompt_one_shot_input),
            AIMessage(query_prompt_one_shot_output),
            HumanMessage(query_prompt_template.format(text)),
        ]
    )
    query_ner_messages = query_ner_prompts.format_prompt()

    json_mode = False
    if isinstance(client, ChatOpenAI):  # JSON mode
        chat_completion = client.invoke(
            query_ner_messages.to_messages(),
            temperature=0,
            max_tokens=300,
            stop=["\n\n"],
            response_format={"type": "json_object"},
        )
        response_content = chat_completion.content
        total_tokens = chat_completion.response_metadata["token_usage"]["total_tokens"]
        json_mode = True
    elif isinstance(client, ChatOllama) or isinstance(client, ChatLlamaCpp):
        response_content = client.invoke(query_ner_messages.to_messages())
        response_content = extract_json_dict(response_content)
        total_tokens = len(response_content.split())
    else:  # no JSON mode
        chat_completion = client.invoke(
            query_ner_messages.to_messages(),
            temperature=0,
            max_tokens=300,
            stop=["\n\n"],
        )
        response_content = chat_completion.content
        response_content = extract_json_dict(response_content)
        total_tokens = chat_completion.response_metadata["token_usage"]["total_tokens"]

    if not json_mode:
        try:
            assert "named_entities" in response_content
            response_content = str(response_content)
        except Exception as e:
            print("Query NER exception", e)
            response_content = {"named_entities": []}

    return response_content, total_tokens


def run_ner_on_texts(llm, model_name, texts):
    ner_output = []
    total_cost = 0

    client = init_langchain_model(llm, model_name)

    for text in tqdm(texts):
        ner, cost = named_entity_recognition(client, text)
        ner_output.append(ner)
        total_cost += cost

    return ner_output, total_cost


def query_extraction(args):
    dataset = args.dataset
    model_name = args.model_name

    output_file = f"data/{dataset}/tmp/{dataset}_queries.named_entity_output.tsv"

    client = init_langchain_model(args.llm, model_name)  # LangChain model
    try:
        queries_df = pd.read_json(f"data/{dataset}/raw/dataset.json")

        if "hotpotqa" in dataset or dataset in ["custom", "demo"]:
            queries_df = queries_df[["question"]]
            queries_df["0"] = queries_df["question"]
            queries_df["query"] = queries_df["question"]
            query_name = "query"
        else:
            query_name = "question"

        try:
            output_df = pd.read_csv(output_file, sep="\t")
        except Exception:
            output_df = []

        if len(queries_df) != len(output_df):
            queries = queries_df[query_name].values

            # for multi-processing split
            num_processes = args.num_processes
            splits = np.array_split(range(len(queries)), num_processes)

            data_splits = []
            for split in splits:
                data_splits.append([queries[i] for i in split])

            if num_processes == 1:
                outputs = [run_ner_on_texts(client, data_splits[0])]
            else:
                partial_func = partial(run_ner_on_texts, client)
                with Pool(processes=num_processes) as pool:
                    outputs = pool.map(partial_func, data_splits)

            chatgpt_total_tokens = 0
            query_triples = []

            for output in outputs:
                query_triples.extend(output[0])
                chatgpt_total_tokens += output[1]

            0.002 * chatgpt_total_tokens / 1000

            queries_df["triples"] = query_triples
            queries_df.to_csv(output_file, sep="\t")
            print("Passage NER saved to", output_file)
        else:
            print("Passage NER already saved to", output_file)
    except Exception as e:
        print("No queries will be processed for later retrieval.", e)


def named_entity_extraction_parallel(
    model_name: str,
    llm: str,
    dataset: str,
    num_processes: int,
) -> None:
    output_file = f"data/{dataset}/tmp/{dataset}_queries.named_entity_output.tsv"

    try:
        queries_df_train = pd.read_json(f"data/{dataset}/raw/train.json")
        queries_df_test = pd.read_json(f"data/{dataset}/raw/test.json")

        queries_df = pd.concat([queries_df_train, queries_df_test], ignore_index=True)

        if "hotpotqa" in dataset or dataset in ["custom", "demo"]:
            queries_df = queries_df[["question"]]
            queries_df["0"] = queries_df["question"]
            queries_df["query"] = queries_df["question"]
            query_name = "query"
        else:
            query_name = "question"

        try:
            output_df = pd.read_csv(output_file, sep="\t")
        except Exception:
            output_df = []

        if len(queries_df) != len(output_df):
            queries = queries_df[query_name].values

            # for multi-processing split
            num_processes = num_processes
            splits = np.array_split(range(len(queries)), num_processes)

            data_splits = []
            for split in splits:
                data_splits.append([queries[i] for i in split])

            if num_processes == 1:
                outputs = [run_ner_on_texts(llm, model_name, data_splits[0])]
            else:
                partial_func = partial(run_ner_on_texts, llm, model_name)
                with Pool(processes=num_processes) as pool:
                    outputs = pool.map(partial_func, data_splits)

            chatgpt_total_tokens = 0
            query_triples = []

            for output in outputs:
                query_triples.extend(output[0])
                chatgpt_total_tokens += output[1]

            0.002 * chatgpt_total_tokens / 1000

            queries_df["triples"] = query_triples
            queries_df.to_csv(output_file, sep="\t")
            print("Passage NER saved to", output_file)
        else:
            print("Passage NER already saved to", output_file)
    except Exception as e:
        print("No queries will be processed for later retrieval.", e)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=str)
    parser.add_argument(
        "--llm", type=str, default="openai", help="LLM, e.g., 'openai' or 'together'"
    )
    parser.add_argument(
        "--model_name",
        type=str,
        default="gpt-3.5-turbo-1106",
        help="Specific model name",
    )
    parser.add_argument(
        "--num_processes", type=int, default=1, help="Number of processes"
    )

    args = parser.parse_args()

    dataset = args.dataset
    model_name = args.model_name
